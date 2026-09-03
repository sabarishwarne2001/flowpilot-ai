"""ARCH-18 — platform COGS, unit economics and supplier reconciliation.

    GET  /admin/cogs/margins/summary            platform revenue vs COGS
    GET  /admin/cogs/margins/tenants            per-tenant ranking
    GET  /admin/cogs/margins/providers          modelled COGS by supplier
    GET  /admin/cogs/rate-card                  price book with cost basis
    GET  /admin/cogs/supplier-invoices          invoice list
    POST /admin/cogs/supplier-invoices          ingest one
    POST /admin/cogs/supplier-invoices/{id}/reconcile
    GET  /admin/cogs/supplier-invoices/{id}/reconciliations
    POST /admin/cogs/reconciliations/{id}/accept

Every route is cross-tenant. The superadmin dependency is declared once on the
router below rather than repeated on each endpoint, because the failure mode of
per-endpoint gating is a route added in six months whose author copied the
decorator but not its dependency argument — and the resulting hole exposes
every tenant's cost structure to whoever finds it.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy import true as sa_true
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_read_db, require_superadmin
from app.core.config import settings
from app.models.user import User
from app.models.reconciliation import ReconciliationRun
from app.models.supplier_cogs import (
    METHOD_ARCH14_SELL_SIDE,
    METHOD_ARCH18_SUPPLIER_COST,
    SupplierInvoice,
    SupplierReconciliation,
)
from app.services.reconciliation import statement_intake
from app.schemas.cogs import (
    AcceptVarianceRequest,
    ConsolidatedReconciliationResponse,
    MarginFiguresResponse,
    MarginOrder,
    PlatformMarginSummaryResponse,
    ProviderCostEntry,
    ProviderCostResponse,
    RateCardEntry,
    RateCardResponse,
    ReconcileRequest,
    SupplierInvoiceCreate,
    SupplierInvoiceListResponse,
    SupplierInvoiceResponse,
    SupplierReconciliationResponse,
    TenantEconomicsEntry,
    TenantEconomicsResponse,
)
from app.services import cost_basis_service, margin_service, pricing_service
from app.services import supplier_reconciliation_service as recon
from app.services.margin_service import MarginFigures

logger = logging.getLogger("app.api.v1.admin.cogs")

router = APIRouter(
    prefix="/admin/cogs",
    tags=["Platform COGS"],
    dependencies=[Depends(require_superadmin)],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_window(
    period_start: Optional[datetime],
    period_end: Optional[datetime],
) -> tuple[datetime, datetime]:
    """Default to a trailing window; refuse an inverted one.

    The upper bound is exclusive throughout the margin path. That differs from
    `supplier_invoices.period_end`, which is an inclusive date because that is
    how a supplier writes an invoice. Two conventions in one phase is a real
    cost, and the alternative — forcing supplier invoices onto exclusive bounds
    — means transcribing "31 Jul" as "1 Aug" by hand every month, which is a
    transcription error waiting to happen against a financial figure.
    """
    now = datetime.now(timezone.utc)
    end = period_end or now
    start = period_start or (
        end - timedelta(days=int(getattr(settings, "COGS_DEFAULT_WINDOW_DAYS", 30)))
    )

    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    if end <= start:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="period_end must be after period_start.",
        )
    return start, end


def _figures(figures: MarginFigures) -> MarginFiguresResponse:
    return MarginFiguresResponse(
        revenue_micros=figures.revenue_micros,
        attributed_revenue_micros=figures.attributed_revenue_micros,
        cost_basis_micros=figures.cost_basis_micros,
        unknown_cost_revenue_micros=figures.unknown_cost_revenue_micros,
        gross_margin_micros=figures.gross_margin_micros,
        gross_margin_ratio=figures.gross_margin_ratio,
        unknown_cost_share=figures.unknown_cost_share,
        soft_cost_share=figures.soft_cost_share,
        event_count=figures.event_count,
        known_cost_event_count=figures.known_cost_event_count,
        unknown_cost_event_count=figures.unknown_cost_event_count,
        is_trustworthy=figures.is_trustworthy,
    )


def _reconciliation_dto(row) -> SupplierReconciliationResponse:
    return SupplierReconciliationResponse(
        id=row.id,
        supplier_invoice_id=row.supplier_invoice_id,
        modelled_total_micros=int(row.modelled_total_micros),
        variance_micros=int(row.variance_micros),
        variance_ratio=(
            float(row.variance_ratio) if row.variance_ratio is not None else None
        ),
        status=row.status,
        modelled_event_count=int(row.modelled_event_count),
        unknown_cost_event_count=int(row.unknown_cost_event_count),
        cost_basis_method=row.cost_basis_method,
        # Read from the model property rather than recomputed here, so there is
        # one definition of "authoritative" in the codebase.
        is_authoritative_cost=bool(row.is_authoritative_cost),
        note=row.note,
        reconciled_at=row.reconciled_at,
        reconciled_by_user_id=row.reconciled_by_user_id,
    )


def _invoice_dto(db: Session, invoice) -> SupplierInvoiceResponse:
    latest = recon.latest_reconciliation(db, supplier_invoice_id=invoice.id)
    return SupplierInvoiceResponse(
        id=invoice.id,
        provider=invoice.provider,
        invoice_reference=invoice.invoice_reference,
        period_start=invoice.period_start,
        period_end=invoice.period_end,
        invoiced_total_micros=int(invoice.invoiced_total_micros),
        currency=invoice.currency,
        raw_document_file_id=invoice.raw_document_file_id,
        ingested_at=invoice.ingested_at,
        ingested_by_user_id=invoice.ingested_by_user_id,
        notes=invoice.notes,
        latest_reconciliation=_reconciliation_dto(latest) if latest else None,
        origin=str(
            (invoice.details or {}).get("origin", statement_intake.ORIGIN_OPERATOR_UPLOAD)
        ),
        superseded_invoice_id=(
            uuid.UUID(str((invoice.details or {})["supersedes_invoice_id"]))
            if (invoice.details or {}).get("supersedes_invoice_id")
            else None
        ),
    )


# ---------------------------------------------------------------------------
# Margins
# ---------------------------------------------------------------------------


@router.get(
    "/margins/summary",
    response_model=PlatformMarginSummaryResponse,
    summary="Platform revenue, COGS and gross margin",
)
def get_margin_summary(
    period_start: Optional[datetime] = Query(default=None),
    period_end: Optional[datetime] = Query(default=None),
    db: Session = Depends(get_read_db),
) -> PlatformMarginSummaryResponse:
    start, end = _resolve_window(period_start, period_end)

    summary = margin_service.platform_summary(
        db, period_start=start, period_end=end
    )

    return PlatformMarginSummaryResponse(
        period_start=summary.period_start,
        period_end=summary.period_end,
        currency=summary.currency,
        organization_count=summary.organization_count,
        figures=_figures(summary.figures),
    )


@router.get(
    "/margins/tenants",
    response_model=TenantEconomicsResponse,
    summary="Per-tenant unit economics, worst margin first",
)
def get_tenant_economics(
    period_start: Optional[datetime] = Query(default=None),
    period_end: Optional[datetime] = Query(default=None),
    order: MarginOrder = Query(default="MARGIN_ASC"),
    limit: int = Query(default=50, ge=1),
    db: Session = Depends(get_read_db),
) -> TenantEconomicsResponse:
    start, end = _resolve_window(period_start, period_end)

    ceiling = int(getattr(settings, "COGS_TENANT_RANKING_MAX", 500))
    capped = min(limit, ceiling)

    entries = margin_service.tenant_economics(
        db,
        period_start=start,
        period_end=end,
        limit=capped,
        order=order,
    )

    return TenantEconomicsResponse(
        period_start=start,
        period_end=end,
        currency="USD",
        order=order,
        entries=[
            TenantEconomicsEntry(
                organization_id=entry.organization_id,
                organization_name=entry.organization_name,
                organization_slug=entry.organization_slug,
                figures=_figures(entry.figures),
            )
            for entry in entries
        ],
    )


@router.get(
    "/margins/providers",
    response_model=ProviderCostResponse,
    summary="Modelled COGS by supplier",
)
def get_provider_costs(
    period_start: Optional[datetime] = Query(default=None),
    period_end: Optional[datetime] = Query(default=None),
    db: Session = Depends(get_read_db),
) -> ProviderCostResponse:
    start, end = _resolve_window(period_start, period_end)

    entries = margin_service.provider_costs(
        db, period_start=start, period_end=end
    )

    return ProviderCostResponse(
        period_start=start,
        period_end=end,
        entries=[
            ProviderCostEntry(
                provider=entry.provider,
                cost_basis_micros=entry.cost_basis_micros,
                revenue_micros=entry.revenue_micros,
                event_count=entry.event_count,
                unknown_cost_event_count=entry.unknown_cost_event_count,
            )
            for entry in entries
        ],
    )


@router.get(
    "/rate-card",
    response_model=RateCardResponse,
    summary="The price book in force, with supplier cost basis",
)
def get_rate_card(
    at: Optional[datetime] = Query(default=None),
    db: Session = Depends(get_read_db),
) -> RateCardResponse:
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    coverage = cost_basis_service.coverage(db, at=moment)
    book = pricing_service.book_in_force(db, at=moment)

    if book is None:
        return RateCardResponse(
            entry_count=0,
            with_cost_basis=0,
            hard_cost_basis=0,
            coverage_ratio=None,
            entries=[],
        )

    entries: list[RateCardEntry] = []
    for key, snapshot in book.entries.items():
        event_type, provider, model_key, tier_key = key
        cost = getattr(snapshot, "cost_basis_micros", None)
        price = Decimal(str(snapshot.unit_price_micros))
        entries.append(
            RateCardEntry(
                event_type=event_type,
                provider=provider,
                model=model_key or None,
                tier_key=tier_key or None,
                unit=snapshot.unit,
                unit_price_micros=price,
                cost_basis_micros=cost,
                cost_basis_source=getattr(snapshot, "cost_basis_source", None),
                unit_margin_micros=(
                    price - Decimal(str(cost)) if cost is not None else None
                ),
            )
        )

    entries.sort(key=lambda e: (e.provider, e.event_type, e.model or ""))

    return RateCardResponse(
        price_book_id=book.id,
        price_book_version=book.version,
        currency=book.currency,
        effective_from=book.effective_from,
        entry_count=int(coverage["entry_count"]),
        with_cost_basis=int(coverage["with_cost_basis"]),
        hard_cost_basis=int(coverage["hard_cost_basis"]),
        coverage_ratio=coverage["coverage_ratio"],
        entries=entries,
    )


# ---------------------------------------------------------------------------
# Supplier invoices
# ---------------------------------------------------------------------------


@router.get(
    "/supplier-invoices",
    response_model=SupplierInvoiceListResponse,
    summary="Supplier invoices with their latest reconciliation",
)
def list_supplier_invoices(
    provider: Optional[str] = Query(default=None, max_length=32),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_read_db),
) -> SupplierInvoiceListResponse:
    invoices = recon.list_invoices(db, provider=provider, limit=limit)
    return SupplierInvoiceListResponse(
        entries=[_invoice_dto(db, invoice) for invoice in invoices]
    )


@router.post(
    "/supplier-invoices",
    response_model=SupplierInvoiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a supplier invoice",
)
def create_supplier_invoice(
    payload: SupplierInvoiceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_superadmin),
) -> SupplierInvoiceResponse:
    try:
        invoice = recon.ingest_invoice(
            db,
            provider=payload.provider,
            period_start=payload.period_start,
            period_end=payload.period_end,
            invoiced_total_micros=payload.invoiced_total_micros,
            currency=payload.currency,
            invoice_reference=payload.invoice_reference,
            raw_document_file_id=payload.raw_document_file_id,
            notes=payload.notes,
            ingested_by_user_id=current_user.id,
        )
    except recon.SupplierInvoiceExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except recon.InvalidAttachmentError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except recon.SupplierReconciliationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    db.commit()
    db.refresh(invoice)
    return _invoice_dto(db, invoice)


@router.post(
    "/supplier-invoices/{supplier_invoice_id}/reconcile",
    response_model=SupplierReconciliationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Compare an invoice against modelled COGS",
)
def reconcile_supplier_invoice(
    supplier_invoice_id: uuid.UUID,
    payload: ReconcileRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_superadmin),
) -> SupplierReconciliationResponse:
    try:
        row = recon.reconcile(
            db,
            supplier_invoice_id=supplier_invoice_id,
            threshold_ratio=payload.threshold_ratio,
            note=payload.note,
            reconciled_by_user_id=current_user.id,
            force=payload.force,
        )
    except recon.SupplierInvoiceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except recon.PeriodNotClosedError as exc:
        # 409 rather than 400: the request is well-formed and will succeed
        # unchanged once the period closes. That is a state conflict, not a
        # client error, and the distinction matters to whatever retries it.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except recon.SupplierReconciliationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    db.commit()
    db.refresh(row)
    return _reconciliation_dto(row)


@router.get(
    "/supplier-invoices/{supplier_invoice_id}/reconciliations",
    response_model=list[SupplierReconciliationResponse],
    summary="Reconciliation history for one invoice",
)
def list_invoice_reconciliations(
    supplier_invoice_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_read_db),
) -> list[SupplierReconciliationResponse]:
    rows = recon.list_reconciliations(
        db, supplier_invoice_id=supplier_invoice_id, limit=limit
    )
    return [_reconciliation_dto(row) for row in rows]


@router.post(
    "/reconciliations/{reconciliation_id}/accept",
    response_model=SupplierReconciliationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Sign off a variance",
)
def accept_variance(
    reconciliation_id: uuid.UUID,
    payload: AcceptVarianceRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_superadmin),
) -> SupplierReconciliationResponse:
    try:
        row = recon.accept(
            db,
            reconciliation_id=reconciliation_id,
            note=payload.note,
            accepted_by_user_id=current_user.id,
        )
    except recon.SupplierReconciliationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    db.commit()
    db.refresh(row)
    return _reconciliation_dto(row)


@router.get(
    "/reconciliations/consolidated",
    response_model=ConsolidatedReconciliationResponse,
    summary="The single authoritative cost-variance view (ARCH-24)",
)
def consolidated_reconciliations(
    provider: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_read_db),
    current_user: User = Depends(require_superadmin),
) -> ConsolidatedReconciliationResponse:
    """Supplier cost variance, from the one engine that computes it.

    ARCH-24 Tranche 3. Superadmin-gated: this is platform margin data and is
    not customer-facing under any circumstances.

    The ARCH-14 runs are reported here as a *count only*, deliberately. Their
    drift figures are denominated in customer price and would be read as cost
    variance by anyone who saw them next to these rows. If you need them, they
    are on the reconciliation-runs endpoint, labelled.
    """
    rows = (
        db.execute(
            select(SupplierReconciliation)
            .join(
                SupplierInvoice,
                SupplierInvoice.id == SupplierReconciliation.supplier_invoice_id,
            )
            .where(
                SupplierInvoice.provider == provider.strip().lower()
                if provider
                else sa_true()
            )
            .order_by(SupplierReconciliation.reconciled_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )

    sell_side = int(
        db.execute(
            select(func.count(ReconciliationRun.id)).where(
                ReconciliationRun.cost_basis_method == METHOD_ARCH14_SELL_SIDE,
                (
                    ReconciliationRun.provider == provider.strip().lower()
                    if provider
                    else sa_true()
                ),
            )
        ).scalar_one()
        or 0
    )

    return ConsolidatedReconciliationResponse(
        entries=[_reconciliation_dto(row) for row in rows],
        authoritative_method=METHOD_ARCH18_SUPPLIER_COST,
        sell_side_run_count=sell_side,
    )


__all__ = ["router"]