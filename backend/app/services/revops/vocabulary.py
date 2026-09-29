"""ARCH-50 RevOps — the closed vocabulary. ARCH50-S1:revops-vocabulary

The migration (arch50_step1_sovereign_revops) carries its own copy in its CHECKs; verify_arch50 T2 proves the
two agree, and W3 proves the console's copy agrees with both.
"""

from __future__ import annotations

CURRENCIES: tuple[str, ...] = ("USD", "INR")
#: Minor units per major unit is 100 for both; everything inside is micros (1e-6 of a major unit).
MICROS_PER_MINOR_UNIT = 10_000
PLAN_INTERVALS: tuple[str, ...] = ("month", "year")
CONTRACT_INTERVALS: tuple[str, ...] = ("month", "quarter", "year")
MONTHS_IN: dict[str, int] = {"month": 1, "quarter": 3, "year": 12}
BOOK_STATUSES: tuple[str, ...] = ("DRAFT", "PUBLISHED", "RETIRED")
PROMO_DURATIONS: tuple[str, ...] = ("ONCE", "REPEATING", "FOREVER")
REDEMPTION_STATUSES: tuple[str, ...] = ("RESERVED", "REDEEMED", "RELEASED", "EXPIRED")
CONTRACT_STATUSES: tuple[str, ...] = ("DRAFT", "ACTIVE", "ENDED", "CANCELLED")
CONTRACT_END_REASONS: tuple[str, ...] = ("TERM_ENDED", "CANCELLED", "SUPERSEDED", "NON_PAYMENT")
INVOICE_STATUSES: tuple[str, ...] = ("ISSUED", "PAID", "VOID")
GATEWAYS: tuple[str, ...] = ("STRIPE", "DODO")
#: A checkout's promo reservation lapses if no subscription starts within this.
RESERVATION_TTL_HOURS = 24
MAX_CONTRACT_TERM_MONTHS = 60
#: Refusal codes the console branches on.
CODES: tuple[str, ...] = (
    "BOOK_NOT_DRAFT", "BOOK_EMPTY", "BOOK_UNKNOWN_TIER", "BOOK_CODE_TAKEN", "PRICE_NOT_SOLD",
    "PROMO_UNKNOWN", "PROMO_INACTIVE", "PROMO_EXPIRED", "PROMO_EXHAUSTED", "PROMO_NOT_APPLICABLE",
    "PROMO_CURRENCY_MISMATCH", "PROMO_FIRST_SUBSCRIPTION_ONLY", "PROMO_ALREADY_USED", "PROMO_NOT_AVAILABLE_ONLINE",
    "PROMO_CODE_TAKEN", "CONTRACT_NOT_DRAFT", "CONTRACT_NOT_ACTIVE", "CONTRACT_ACTIVE_EXISTS",
    "SUBSCRIPTION_ACTIVE", "CONTRACT_NUMBER_TAKEN", "CONTRACT_TERM_TOO_LONG", "TIER_NOT_PUBLISHED",
    "INVOICE_NOT_ISSUED", "NOT_FOUND",
)
