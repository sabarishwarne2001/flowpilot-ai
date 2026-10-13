"""The published plans as a customer reads them (campaign session 4).

One place turns a published tier version into what a plan card shows, so the
in-app plan cards (`GET .../billing/plans`) and the public price list behind
the `/pricing` page (`GET /api/v1/pricing`) cannot disagree about a number:
both read `quota_service.list_published_tiers`, the published plan price
books, and the helpers below.

WHAT THE PUBLIC PRICE LIST CARRIES, AND WHAT IT DOES NOT
========================================================
Per plan: its key and name, every price it is sold at (interval, currency,
amount per seat in minor units), whether its allowances are per seat, the
counts a customer plans by (TENANCY-AND-PLANS.md 4.2 and 4.4: documents, OCR
pages, assistant messages, storage, seats, workspaces, file size, pages per
document) and the capabilities and add-ons its version grants. Plus the length
of the free trial (`trial_service.trial_length`, owner decision N-052).

Not carried: anything about an organization or a person, gateway price ids
(internal identifiers of the payment provider's products), tier version ids,
and the token and cost ceilings, which TENANCY-AND-PLANS.md calls the
platform's safety net above the counts rather than something a customer
plans by. The in-app plan card still lists every allowance.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional, Union

from sqlalchemy.orm import Session

#: The allowances the public price list shows, in plan-card order (`ENTITLEMENT_ORDER` in
#: frontend/src/types/planEntitlements.ts). `llm.platform_key` is the "AI included" line.
PUBLIC_ALLOWANCE_KEYS: tuple[str, ...] = (
    "document.upload",
    "ocr.page",
    "assistant.message",
    "limit.seats",
    "limit.workspaces",
    "limit.file_size_mb",
    "limit.pages_per_document",
    "storage.gb_month",
    "llm.platform_key",
)

#: A row whose presence IS the grant (`capability_gate`, `entitlement_service.tier_grants`).
FEATURE_PREFIXES: tuple[str, ...] = ("capability.", "addon.")

#: An ABSENT row is a promise too (TENANCY-AND-PLANS.md 4.2 and 4.4). A meter with no row has
#: no ceiling of its own and a plan with no workspace limit has unlimited workspaces: both
#: are listed with `limit_quantity` None. A plan with no file-size or page limit takes the
#: platform's maximum, the figure `plan_admission` enforces. An absent `limit.seats` means
#: the seats are bought (`per_seat`), and an absent `llm.platform_key` is not granted: both
#: are left out.
NO_CEILING_WHEN_ABSENT: frozenset[str] = frozenset(
    {"document.upload", "ocr.page", "assistant.message", "storage.gb_month", "limit.workspaces"}
)
_MIB = 1024 * 1024


def _platform_maximum(key: str) -> Optional[int]:
    """The platform-wide ceiling `plan_admission` applies to a static limit, if it has one."""
    from app.core.config import settings

    if key == "limit.file_size_mb":
        return int(settings.MAX_UPLOAD_SIZE) // _MIB
    if key == "limit.pages_per_document":
        return int(settings.MAX_DOCUMENT_PAGES)
    return None


def allowance_quantity(value: Optional[Decimal]) -> Optional[Union[int, float]]:
    """A tier entry's quantity for a client: whole numbers stay whole, fractions are kept.

    Free stores 0.25 GB; an `int()` here made the plan card read 0 (and print "0 MB").
    None stays None: no ceiling.
    """
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def prices_for_tier(tier: Any, book_prices: Iterable[Any]) -> list[Any]:
    """Every (interval, currency) a published tier version is sold at.

    The published plan price books first (ARCH-50: annual, INR), then the tier
    version's own price when no book covers its (interval, currency). The same
    precedence `price_books.resolve_price` applies at checkout, so a price shown
    here is the price charged.
    """
    from app.services.revops.price_books import PlanPrice

    options = [price for price in book_prices if price.tier_key == tier.key]
    own_interval = tier.billing_interval or "month"
    own_currency = tier.currency or "USD"
    if tier.unit_amount_micros is not None and not any(
        (price.interval, price.currency) == (own_interval, own_currency) for price in options
    ):
        options.append(
            PlanPrice(
                tier.key,
                own_interval,
                own_currency,
                int(tier.unit_amount_micros),
                tier.gateway_price_id,
                None,
                None,
                "TIER",
            )
        )
    return options


def _public_allowances(by_key: dict[str, Any]) -> list[dict[str, Any]]:
    """The counts a customer plans by, as the product enforces them (see NO_CEILING_WHEN_ABSENT)."""
    allowances: list[dict[str, Any]] = []
    for key in PUBLIC_ALLOWANCE_KEYS:
        entry = by_key.get(key)
        platform = _platform_maximum(key)
        if entry is None:
            if platform is not None:
                allowances.append(
                    {"key": key, "limit_quantity": platform, "period": "MONTH", "overage_policy": "REFUSE"}
                )
            elif key in NO_CEILING_WHEN_ABSENT:
                allowances.append({"key": key, "limit_quantity": None, "period": "MONTH", "overage_policy": None})
            continue
        quantity = allowance_quantity(entry.max_quantity)
        if platform is not None and quantity is not None:
            quantity = min(platform, int(quantity))  # never above the platform's (plan_admission)
        allowances.append(
            {
                "key": key,
                "limit_quantity": quantity,
                "period": getattr(entry.period, "value", str(entry.period)),
                "overage_policy": entry.overage_policy,
            }
        )
    return allowances


def public_catalog(db: Session, *, at: Optional[datetime] = None) -> dict[str, Any]:
    """The public price list: every published plan, its prices and allowances, and the trial."""
    from app.services import quota_service, trial_service
    from app.services.revops import price_books

    moment = at or datetime.now(timezone.utc)
    book_prices = price_books.published_prices(db)
    plans: list[dict[str, Any]] = []
    for tier in quota_service.list_published_tiers(db, at=moment):
        by_key = {entry.limit_key: entry for entry in tier.entries}
        plans.append(
            {
                "key": tier.key,
                "display_name": tier.display_name,
                "is_priced": tier.is_priced,
                # The rule `quota_service.seat_factor` applies: a priced, non-zero plan
                # is per seat; Free (and an unpriced tier) is per organization.
                "per_seat": bool(tier.unit_amount_micros),
                "prices": [
                    {"interval": price.interval, "currency": price.currency, "unit_amount": price.unit_amount_minor}
                    for price in prices_for_tier(tier, book_prices)
                ],
                "allowances": _public_allowances(by_key),
                "features": sorted(key for key in by_key if key.startswith(FEATURE_PREFIXES)),
            }
        )
    return {
        "as_of": moment,
        # The length the product enforces (`trial_service.trial_length`, N-052).
        "trial": {"plan_key": quota_service.FREE_TIER_KEY, "days": trial_service.trial_length().days},
        "plans": plans,
    }


__all__ = [
    "FEATURE_PREFIXES",
    "NO_CEILING_WHEN_ABSENT",
    "PUBLIC_ALLOWANCE_KEYS",
    "allowance_quantity",
    "prices_for_tier",
    "public_catalog",
]
