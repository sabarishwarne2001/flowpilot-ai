"""ARCH50-S1:models — RevOps: plan price books (annual intervals, INR), promo codes, invoiced enterprise
contracts and revenue snapshots.

The schema (CHECKs, the published-book immutability and one-gateway-price-one-plan triggers, the partial UNIQUEs)
lives in alembic/versions/arch50_step1_sovereign_revops.py; these mappings match it exactly (verify_arch50 D2).

Two price books, deliberately separate: `price_books` (ARCH-14) prices USAGE -- per event, in the billing
account's currency, and ARCH-15's F7 trigger still refuses a second usage currency. `plan_price_books` price the
PLAN -- the subscription a customer checks out -- per tier, interval and currency. An INR plan price is what the
gateway charges for the plan; it does not make usage invoices bilingual.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid(*args: Any, **kw: Any) -> Any:
    return mapped_column(PgUUID(as_uuid=True), *args, **kw)


def _ts(nullable: bool = False, server_now: bool = False) -> Any:
    if nullable:
        return mapped_column(DateTime(timezone=True), nullable=True)
    if server_now:
        return mapped_column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))
    return mapped_column(DateTime(timezone=True), nullable=False)


def _user_fk() -> Any:
    return _uuid(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class PlanPriceBook(Base):
    __tablename__ = "plan_price_books"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default=sa_text("'DRAFT'"))
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    content_digest: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    published_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    published_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    retired_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    created_at: Mapped[datetime] = _ts(server_now=True)


class PlanPriceBookEntry(Base):
    __tablename__ = "plan_price_book_entries"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = _uuid(ForeignKey("plan_price_books.id", ondelete="CASCADE"), nullable=False)
    tier_key: Mapped[str] = mapped_column(String(32), nullable=False)
    billing_interval: Mapped[str] = mapped_column(String(8), nullable=False)
    unit_amount_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    gateway_price_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = _ts(server_now=True)


class PromoCode(Base):
    __tablename__ = "promo_codes"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    percent_off: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    amount_off_micros: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    duration: Mapped[str] = mapped_column(String(10), nullable=False)
    duration_in_months: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_redemptions: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    times_redeemed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    redeem_by: Mapped[Optional[datetime]] = _ts(nullable=True)
    applies_to_tiers: Mapped[Optional[list[str]]] = mapped_column(ARRAY(String(32)), nullable=True)
    applies_to_intervals: Mapped[Optional[list[str]]] = mapped_column(ARRAY(String(8)), nullable=True)
    first_subscription_only: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_text("false"))
    gateway_coupon_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_text("true"))
    created_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    created_at: Mapped[datetime] = _ts(server_now=True)


class PromoRedemption(Base):
    __tablename__ = "promo_redemptions"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    promo_code_id: Mapped[uuid.UUID] = _uuid(ForeignKey("promo_codes.id", ondelete="RESTRICT"), nullable=False)
    organization_id: Mapped[uuid.UUID] = _uuid(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    tier_key: Mapped[str] = mapped_column(String(32), nullable=False)
    billing_interval: Mapped[str] = mapped_column(String(8), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    list_amount_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    discount_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    contract_id: Mapped[Optional[uuid.UUID]] = _uuid(ForeignKey("enterprise_contracts.id", ondelete="SET NULL"),
                                                     nullable=True)
    reserved_at: Mapped[datetime] = _ts()
    expires_at: Mapped[datetime] = _ts()
    redeemed_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    released_at: Mapped[Optional[datetime]] = _ts(nullable=True)


class CheckoutSelection(Base):
    """What a checkout sold (tier, interval, currency, price, seats) -- the gateway's subscription row does not
    record it, and revenue metrics must not guess an annual INR plan's amount from a monthly USD tier price."""

    __tablename__ = "checkout_selections"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = _uuid(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    tier_key: Mapped[str] = mapped_column(String(32), nullable=False)
    billing_interval: Mapped[str] = mapped_column(String(8), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    book_entry_id: Mapped[Optional[uuid.UUID]] = _uuid(ForeignKey("plan_price_book_entries.id",
                                                                  ondelete="SET NULL"), nullable=True)
    unit_amount_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seats: Mapped[int] = mapped_column(Integer, nullable=False)
    promo_redemption_id: Mapped[Optional[uuid.UUID]] = _uuid(ForeignKey("promo_redemptions.id",
                                                                        ondelete="SET NULL"), nullable=True)
    gateway: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = _ts(server_now=True)


class EnterpriseContract(Base):
    __tablename__ = "enterprise_contracts"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = _uuid(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    contract_number: Mapped[str] = mapped_column(String(32), nullable=False)
    tier_key: Mapped[str] = mapped_column(String(32), nullable=False)
    seats: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    billing_interval: Mapped[str] = mapped_column(String(8), nullable=False)
    amount_per_period_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tax_rate_bps: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    term_start: Mapped[date] = mapped_column(Date, nullable=False)
    term_end: Mapped[date] = mapped_column(Date, nullable=False)
    payment_terms_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("30"))
    po_number: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    billing_email: Mapped[Optional[str]] = mapped_column(String(254), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default=sa_text("'DRAFT'"))
    promo_code_id: Mapped[Optional[uuid.UUID]] = _uuid(ForeignKey("promo_codes.id", ondelete="RESTRICT"),
                                                       nullable=True)
    activated_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    activated_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    ended_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    end_reason: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    created_at: Mapped[datetime] = _ts(server_now=True)
    updated_at: Mapped[datetime] = _ts(server_now=True)


class ContractInvoice(Base):
    __tablename__ = "contract_invoices"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    contract_id: Mapped[uuid.UUID] = _uuid(ForeignKey("enterprise_contracts.id", ondelete="CASCADE"),
                                           nullable=False)
    organization_id: Mapped[uuid.UUID] = _uuid(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    invoice_number: Mapped[str] = mapped_column(String(32), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    discount_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=sa_text("0"))
    tax_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=sa_text("0"))
    total_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, server_default=sa_text("'ISSUED'"))
    issued_at: Mapped[datetime] = _ts()
    due_at: Mapped[datetime] = _ts()
    paid_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    payment_reference: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    void_reason: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = _ts(server_now=True)


class RevenueSnapshot(Base):
    """A closed month's revenue in one currency, frozen by the RevOps sweep (history the live view cannot
    reconstruct once subscriptions change)."""

    __tablename__ = "revenue_snapshots"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    month: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    mrr_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    arr_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    new_mrr_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=sa_text("0"))
    expansion_mrr_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=sa_text("0"))
    contraction_mrr_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=sa_text("0"))
    churned_mrr_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=sa_text("0"))
    active_customers: Mapped[int] = mapped_column(Integer, nullable=False)
    unpriced_customers: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=sa_text("'{}'::jsonb"))
    computed_at: Mapped[datetime] = _ts(server_now=True)
