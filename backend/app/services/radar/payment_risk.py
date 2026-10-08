"""PHASE 4 — payment-risk checks on an invoice (radar, separate store).

Two checks, run for every invoice the radar scans:

BANK_ACCOUNT_CHANGED (HIGH)
    The invoice names a bank account (IBAN or account number) and the
    vendor's most recent earlier invoice in this workspace named a different
    one. A changed payee account on an otherwise familiar invoice is the
    classic business-email-compromise fraud: the money goes to the
    fraudster. Only the last four characters of either account are stored
    or shown; a short hash tells them apart.

ROUND_AMOUNT (LOW)
    The total is a large exact multiple of ROUND_STEP with no minor units.
    Fabricated invoices are disproportionately round. Plenty of real ones
    (retainers, fixed fees) are too, hence LOW: a reviewer confirms or
    dismisses with a reason, as for every radar finding.

Each check writes at most one flag per (document, kind); re-scanning a
document never duplicates a flag or reopens one a reviewer settled.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.document_role import DocumentRole
from app.models.payment_risk import (
    KIND_BANK_ACCOUNT_CHANGED,
    KIND_ROUND_AMOUNT,
    PaymentRiskFlag,
)
from app.models.work_item import WorkItem

ROLE_INVOICE = "INVOICE"
#: F-173. The payee account under every name models give it, most specific first. The check read
#: only the first four, and the platform's extraction answers `vendor_bank_account`: the account on
#: INV-E2E-1002 changed and the radar said no invoice had changed its bank account.
ACCOUNT_FIELDS = (
    "iban", "bank_account_number", "account_number", "bank_account",
    "vendor_bank_account", "vendor_iban", "vendor_account_number", "vendor_bank_account_number",
    "supplier_bank_account", "supplier_iban", "supplier_account_number",
    "beneficiary_account", "beneficiary_iban", "beneficiary_account_number",
    "payee_account", "payee_iban", "payee_account_number", "remit_to_account",
)
#: Objects a model nests the account in ({"bank_details": {"iban": ...}}).
ACCOUNT_CONTAINERS = ("bank_details", "banking_details", "payment_details", "remit_to", "bank", "remittance")
MIN_ACCOUNT_CHARS = 6
PRIOR_INVOICES_EXAMINED = 20
#: A total of at least ROUND_MINIMUM that is an exact multiple of ROUND_STEP.
ROUND_STEP = Decimal("1000")
ROUND_MINIMUM = Decimal("5000")
MICROS = Decimal("1000000")


def _snake(key: Any) -> str:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key).strip())
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _account(entities: Any, *, depth: int = 0) -> Optional[str]:
    if not isinstance(entities, dict) or depth > 1:
        return None
    by_key = {_snake(key): value for key, value in entities.items()}
    for key in ACCOUNT_FIELDS:
        value = by_key.get(key)
        if isinstance(value, dict):
            value = value.get("value")
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            compact = re.sub(r"[^0-9A-Za-z]", "", str(value)).upper()
            if len(compact) >= MIN_ACCOUNT_CHARS:
                return compact
    for key in ACCOUNT_CONTAINERS:
        nested = _account(by_key.get(key), depth=depth + 1)
        if nested is not None:
            return nested
    return None


def _masked(account: str) -> dict[str, str]:
    return {"last4": account[-4:], "display": f"•••• {account[-4:]}",
            "fingerprint": hashlib.sha256(account.encode()).hexdigest()[:12]}


def _flag(db: Session, *, role: DocumentRole, kind: str, severity: str, summary: str,
          details: dict[str, Any], counterpart: Optional[uuid.UUID] = None) -> bool:
    inserted = db.execute(
        pg_insert(PaymentRiskFlag)
        .values(id=uuid.uuid4(), organization_id=role.organization_id, workspace_id=role.workspace_id,
                work_item_id=role.work_item_id, counterpart_work_item_id=counterpart, kind=kind,
                severity=severity, status="OPEN", vendor_key=role.vendor_key, summary=summary, details=details)
        .on_conflict_do_nothing(index_elements=["work_item_id", "kind"])
        .returning(PaymentRiskFlag.id)
    ).first()
    return inserted is not None


def _bank_account_changed(db: Session, *, role: DocumentRole, item: WorkItem) -> bool:
    account = _account(item.extracted_entities)
    if account is None or not role.vendor_key:
        return False
    earlier = db.execute(
        select(WorkItem.id, WorkItem.extracted_entities, WorkItem.original_filename)
        .join(DocumentRole, DocumentRole.work_item_id == WorkItem.id)
        .where(
            DocumentRole.workspace_id == role.workspace_id,
            DocumentRole.role == ROLE_INVOICE,
            DocumentRole.vendor_key == role.vendor_key,
            WorkItem.id != item.id,
            WorkItem.created_at <= item.created_at,
        )
        .order_by(WorkItem.created_at.desc())
        .limit(PRIOR_INVOICES_EXAMINED)
    ).all()
    for prior in earlier:
        previous = _account(prior.extracted_entities)
        if previous is None:
            continue
        if previous == account:
            return False
        old, new = _masked(previous), _masked(account)
        vendor = (item.extracted_entities or {}).get("vendor_name") or role.vendor_key
        return _flag(
            db, role=role, kind=KIND_BANK_ACCOUNT_CHANGED, severity="HIGH",
            summary=(f"{vendor} asks to be paid into account {new['display']}; its previous invoice "
                     f"used {old['display']}. Confirm the change with the vendor by phone before paying."),
            details={"previous_account": old, "new_account": new, "previous_document": prior.original_filename},
            counterpart=prior.id,
        )
    return False


def _round_amount(db: Session, *, role: DocumentRole) -> bool:
    if role.total_micros is None:
        return False
    total = Decimal(role.total_micros) / MICROS
    if total < ROUND_MINIMUM or total % ROUND_STEP != 0:
        return False
    return _flag(
        db, role=role, kind=KIND_ROUND_AMOUNT, severity="LOW",
        summary=(f"The total is exactly {total:,.2f}{' ' + role.currency if role.currency else ''}. "
                 "Exact round sums are common on fabricated invoices; check it against an order or contract."),
        details={"total": f"{total:.2f}", "currency": role.currency, "step": f"{ROUND_STEP:.0f}"},
    )


def scan(db: Session, *, work_item: WorkItem) -> list[str]:
    """Run both checks for one document; returns the kinds newly flagged. Caller commits."""
    from app.api.capability_gate import has_capability
    from app.core import entitlements

    role = db.execute(
        select(DocumentRole).where(DocumentRole.work_item_id == work_item.id)
    ).scalar_one_or_none()
    if role is None or role.role != ROLE_INVOICE:
        return []
    if not has_capability(db, organization_id=role.organization_id,
                          capability_key=entitlements.ANOMALY_RADAR_CAPABILITY):
        return []
    flagged: list[str] = []
    if _bank_account_changed(db, role=role, item=work_item):
        flagged.append(KIND_BANK_ACCOUNT_CHANGED)
    if _round_amount(db, role=role):
        flagged.append(KIND_ROUND_AMOUNT)
    return flagged


__all__ = ["scan"]
