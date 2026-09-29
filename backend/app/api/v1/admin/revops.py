"""ARCH-50 — the operator's RevOps console (platform superadmins only).

    GET  /admin/revops/metrics                         MRR / ARR per currency, movements, receivables, snapshots
    POST /admin/revops/sweep                           run the daily sweep now
    GET  /admin/revops/price-books                     plan price books (USD, INR)
    POST /admin/revops/price-books                     draft one
    GET  /admin/revops/price-books/{id}                one, with its entries
    PUT  /admin/revops/price-books/{id}/entries        set (tier, interval) -> amount, gateway price   [draft only]
    DELETE /admin/revops/price-books/{id}/entries/{e}  remove an entry                                 [draft only]
    POST /admin/revops/price-books/{id}/publish        publish (immutable; retires the currency's previous book)
    GET  /admin/revops/promo-codes                     codes with their redemptions
    POST /admin/revops/promo-codes                     create one
    PUT  /admin/revops/promo-codes/{id}/active         switch it on or off
    GET  /admin/revops/contracts                       invoiced contracts (open and overdue invoice counts)
    POST /admin/revops/contracts                       draft one
    GET  /admin/revops/contracts/{id}                  one, with its invoices
    POST /admin/revops/contracts/{id}/activate         activate (assigns the plan)
    POST /admin/revops/contracts/{id}/issue            issue every period that has started
    POST /admin/revops/contracts/{id}/end              end or cancel
    POST /admin/revops/invoices/{id}/pay               record a payment
    POST /admin/revops/invoices/{id}/void              void with a reason

ARCH50-S1:revops-api. `require_superadmin` on the router (404 for anyone else). Every change to a contract or its
invoices is audited on the customer organization (BILLING_ACCOUNT / UPDATED with an `operation`).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_superadmin
from app.models.user import User
from app.schemas.revops import (ContractEndIn, ContractIn, ContractOut, ContractSummary, InvoicePayIn,
                                InvoiceVoidIn, PriceBookIn, PriceBookOut, PriceBookSummary, PriceEntryIn,
                                PromoActiveIn, PromoCodeIn, PromoCodeOut, RevenueMetricsOut, SweepOut)
from app.services.revops import contracts, metrics, price_books, promos

router = APIRouter(prefix="/admin/revops", tags=["RevOps"], dependencies=[Depends(require_superadmin)])


@router.get("/metrics", response_model=RevenueMetricsOut)
def revenue_metrics(db: Session = Depends(get_db)) -> RevenueMetricsOut:
    return RevenueMetricsOut(**metrics.compute(db))


@router.post("/sweep", response_model=SweepOut)
def run_sweep(db: Session = Depends(get_db)) -> SweepOut:
    out = metrics.sweep(db)
    db.commit()
    return SweepOut(**out)


@router.get("/price-books", response_model=list[PriceBookSummary])
def list_price_books(db: Session = Depends(get_db)) -> list[PriceBookSummary]:
    return [PriceBookSummary(**b) for b in price_books.list_books(db)]


@router.post("/price-books", response_model=PriceBookOut, status_code=status.HTTP_201_CREATED)
def create_price_book(payload: PriceBookIn, db: Session = Depends(get_db),
                      user: User = Depends(require_superadmin)) -> PriceBookOut:
    book = price_books.create_book(db, code=payload.code, currency=payload.currency, notes=payload.notes,
                                   actor_id=user.id)
    db.commit()
    return PriceBookOut(**book)


@router.get("/price-books/{book_id}", response_model=PriceBookOut)
def get_price_book(book_id: uuid.UUID, db: Session = Depends(get_db)) -> PriceBookOut:
    return PriceBookOut(**price_books.book_detail(db, book_id))


@router.put("/price-books/{book_id}/entries", response_model=PriceBookOut)
def set_price_entry(book_id: uuid.UUID, payload: PriceEntryIn, db: Session = Depends(get_db)) -> PriceBookOut:
    book = price_books.set_entry(db, book_id=book_id, tier_key=payload.tier_key, interval=payload.interval,
                                 unit_amount_minor=payload.unit_amount, gateway_price_id=payload.gateway_price_id)
    db.commit()
    return PriceBookOut(**book)


@router.delete("/price-books/{book_id}/entries/{entry_id}", response_model=PriceBookOut)
def delete_price_entry(book_id: uuid.UUID, entry_id: uuid.UUID, db: Session = Depends(get_db)) -> PriceBookOut:
    book = price_books.delete_entry(db, book_id=book_id, entry_id=entry_id)
    db.commit()
    return PriceBookOut(**book)


@router.post("/price-books/{book_id}/publish", response_model=PriceBookOut)
def publish_price_book(book_id: uuid.UUID, db: Session = Depends(get_db),
                       user: User = Depends(require_superadmin)) -> PriceBookOut:
    book = price_books.publish(db, book_id=book_id, actor_id=user.id)
    db.commit()
    return PriceBookOut(**book)


@router.get("/promo-codes", response_model=list[PromoCodeOut])
def list_promo_codes(db: Session = Depends(get_db)) -> list[PromoCodeOut]:
    return [PromoCodeOut(**p) for p in promos.list_codes(db)]


@router.post("/promo-codes", response_model=PromoCodeOut, status_code=status.HTTP_201_CREATED)
def create_promo_code(payload: PromoCodeIn, db: Session = Depends(get_db),
                      user: User = Depends(require_superadmin)) -> PromoCodeOut:
    promo = promos.create_code(db, spec=payload.model_dump(), actor_id=user.id)
    db.commit()
    return PromoCodeOut(**promo)


@router.put("/promo-codes/{promo_id}/active", response_model=PromoCodeOut)
def set_promo_active(promo_id: uuid.UUID, payload: PromoActiveIn, db: Session = Depends(get_db)) -> PromoCodeOut:
    promo = promos.set_active(db, promo_id=promo_id, active=payload.is_active)
    db.commit()
    return PromoCodeOut(**promo)


@router.get("/contracts", response_model=list[ContractSummary])
def list_contracts(db: Session = Depends(get_db)) -> list[ContractSummary]:
    return [ContractSummary(**c) for c in contracts.list_contracts(db)]


@router.post("/contracts", response_model=ContractOut, status_code=status.HTTP_201_CREATED)
def create_contract(payload: ContractIn, db: Session = Depends(get_db),
                    user: User = Depends(require_superadmin)) -> ContractOut:
    contract = contracts.create(db, spec=payload.model_dump(), actor_id=user.id)
    db.commit()
    return ContractOut(**contract)


@router.get("/contracts/{contract_id}", response_model=ContractOut)
def get_contract(contract_id: uuid.UUID, db: Session = Depends(get_db)) -> ContractOut:
    return ContractOut(**contracts.detail(db, contract_id))


@router.post("/contracts/{contract_id}/activate", response_model=ContractOut)
def activate_contract(contract_id: uuid.UUID, db: Session = Depends(get_db),
                      user: User = Depends(require_superadmin)) -> ContractOut:
    contract = contracts.activate(db, contract_id=contract_id, actor_id=user.id)
    db.commit()
    return ContractOut(**contract)


@router.post("/contracts/{contract_id}/issue", response_model=ContractOut)
def issue_contract_invoices(contract_id: uuid.UUID, db: Session = Depends(get_db)) -> ContractOut:
    contracts.issue_due_invoices(db, contract_id=contract_id)
    db.commit()
    return ContractOut(**contracts.detail(db, contract_id))


@router.post("/contracts/{contract_id}/end", response_model=ContractOut)
def end_contract(contract_id: uuid.UUID, payload: ContractEndIn, db: Session = Depends(get_db),
                 user: User = Depends(require_superadmin)) -> ContractOut:
    contract = contracts.end(db, contract_id=contract_id, reason=payload.reason, actor_id=user.id)
    db.commit()
    return ContractOut(**contract)


@router.post("/invoices/{invoice_id}/pay", response_model=ContractOut)
def pay_invoice(invoice_id: uuid.UUID, payload: InvoicePayIn, db: Session = Depends(get_db),
                user: User = Depends(require_superadmin)) -> ContractOut:
    contract = contracts.mark_paid(db, invoice_id=invoice_id, reference=payload.reference, paid_at=payload.paid_at,
                                   actor_id=user.id)
    db.commit()
    return ContractOut(**contract)


@router.post("/invoices/{invoice_id}/void", response_model=ContractOut)
def void_invoice(invoice_id: uuid.UUID, payload: InvoiceVoidIn, db: Session = Depends(get_db),
                 user: User = Depends(require_superadmin)) -> ContractOut:
    contract = contracts.void(db, invoice_id=invoice_id, reason=payload.reason, actor_id=user.id)
    db.commit()
    return ContractOut(**contract)
