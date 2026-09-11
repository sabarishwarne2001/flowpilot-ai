"""ARCH-30 Tranche 1 (T4-F1) — capability entitlements.

THE DEFECT THIS MODULE CLOSES
=============================

ARCH-29 Tranche 2 (D-2) made inference on the platform's provider account a
tier entitlement: a `quota_tier_entries` row with `limit_key =
"llm.platform_key"`, where presence is the grant. The reader was correct —
`model_routing_service._platform_key_entitled` — and so was the seed, which
added the row to every tier.

The writer between them refused it. `quota_service.publish_tier` runs every
entry through `_validate`, and `_validate` accepted exactly two kinds of key:
the wildcard `*` and a billable member of `USAGE_EVENT_TYPES`. A capability is
neither. Executed against the seed's own definitions, all four tiers were
rejected with "'llm.platform_key' is neither the wildcard '*' nor a billable
usage event type", and because `seed_quota_tiers.py` publishes every tier in
one transaction, the rollback took all four with it.

Nothing downstream could notice. `resolve_tier` found no tier carrying the
row, `_platform_key_entitled` answered False — correctly, by its own
fail-closed rule — and every tenant without BYOK was refused inference. Each
component did what it was written to do. The vocabulary between them had no
word for the thing being passed.

It survived `verify_arch29_tranche2.py` because every check in that gate reads
source. G7 there asserts the seed's keys have display labels; nothing asserted
the seed's tiers could be PUBLISHED. `verify_arch30_tranche1.py` G1 executes
the real validator against the real seed rows for exactly that reason.

WHY A SEPARATE VOCABULARY, AND NOT A USAGE EVENT TYPE
=====================================================

The one-line fix is to register `llm.platform_key` in `usage_events._BASE_TYPES`
with `billable=True`. That fix is wrong in four places at once:

  * `quota_service.quota_status` iterates `USAGE_EVENT_TYPES` and would render
    a meter row for a capability, "0 of 0 used", on every usage page;
  * `usage_events._overage_variants` would mint `llm.platform_key.overage`, a
    billable event type for exceeding a permission;
  * `usage_events.resolve` would accept it as an emittable event, so a
    metering bug could record "consumption" of an entitlement;
  * `spend_control_service.effective_limits` would treat the row's
    `max_cost_micros = 0` as a real zero-dollar ceiling if anything ever asked
    for that key.

A meter answers "how much". An entitlement answers "whether". Registering one
as the other makes every consumer of the meter vocabulary wrong in a
different way, and none of them fails loudly. `_assert_disjoint_from_meters`
below makes the two vocabularies structurally unable to share a name: a
collision raises at import, which stops the application from starting rather
than letting it serve with an ambiguous key.

THE ROW SHAPE
=============

`quota_tier_entries` carries CHECK `at_least_one_ceiling`, so an entitlement
row cannot leave both ceilings null. The canonical shape — the one the seed
already writes — is:

    max_cost_micros = 0, max_quantity = NULL, overage_policy = 'REFUSE',
    grace_quantity = NULL, overage_price_tier_key = NULL, period = 'MONTH'

`shape_violation` enforces exactly that. Any other shape means someone
believed the row meters something, and publishing it would encode that belief
in an immutable tier version. Period is pinned to MONTH because the unique
index is `(quota_tier_id, limit_key, period)`: allowing a DAY and a MONTH copy
of the same grant would give a presence check two rows to disagree about.

ADD-ONS (ARCH-30 TRANCHE 2, D-8)
=================================

`addon.custom_domain` and `addon.warehouse_sync` are registered here now that
they have readers: `entitlement_service.tier_grants` for the tier source and
`app.api.addon_gate.require_addon` on every create and maintain endpoint of
the two routers they gate. A tier grants an add-on by carrying the row, in
exactly the canonical shape `llm.platform_key` uses; a PURCHASED add-on is
recorded in `organization_addons` instead, because a purchase is a gateway
subscription with its own lifecycle and a published tier version is immutable.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from app.core.usage_events import TOTAL_COST_KEY, USAGE_EVENT_TYPES

__all__ = [
    "ADDON_KEYS",
    "CANONICAL_MAX_COST_MICROS",
    "CUSTOM_DOMAIN_ADDON",
    "WAREHOUSE_SYNC_ADDON",
    "CANONICAL_OVERAGE_POLICY",
    "CANONICAL_PERIOD",
    "ENTITLEMENT_KEYS",
    "Entitlement",
    "PLATFORM_KEY",
    "is_entitlement_key",
    "shape_violation",
]


@dataclass(frozen=True)
class Entitlement:
    """One capability a tier grants by carrying a row keyed by `name`."""

    name: str
    description: str


#: ARCH-29 D-2. Inference on the PLATFORM's provider account.
#:
#: `model_routing_service.PLATFORM_KEY_LIMIT_KEY` holds the same literal. It is
#: not imported from here, so that this tranche does not edit a module the
#: Tranche 2 gate reads; `verify_arch30_tranche1.py` G4 asserts the two agree.
PLATFORM_KEY: str = "llm.platform_key"

#: ARCH-30 Tranche 2 (D-8). Serving the tenant on its own verified hostname.
CUSTOM_DOMAIN_ADDON: str = "addon.custom_domain"

#: ARCH-30 Tranche 2 (D-8). Scheduled exports into the tenant's warehouse.
WAREHOUSE_SYNC_ADDON: str = "addon.warehouse_sync"

#: Every add-on key. `entitlement_service` asserts its catalog equals this set
#: at import, so a key cannot be registered without a price and a halt effect.
ADDON_KEYS: tuple[str, ...] = (CUSTOM_DOMAIN_ADDON, WAREHOUSE_SYNC_ADDON)

_ENTITLEMENTS: tuple[Entitlement, ...] = (
    Entitlement(
        name=PLATFORM_KEY,
        description=(
            "Inference on the platform's provider account. Withheld, the "
            "tenant must configure BYOK, and model routing refuses rather "
            "than falling back onto the operator's key."
        ),
    ),
    Entitlement(
        name=CUSTOM_DOMAIN_ADDON,
        description=(
            "Custom domains: claim, verify and serve the tenant on its own "
            "hostname. Bundled with Enterprise; purchasable on other plans."
        ),
    ),
    Entitlement(
        name=WAREHOUSE_SYNC_ADDON,
        description=(
            "Warehouse sync: register destinations and run scheduled exports. "
            "Bundled with Enterprise; purchasable on other plans."
        ),
    ),
)

ENTITLEMENT_KEYS: dict[str, Entitlement] = {e.name: e for e in _ENTITLEMENTS}

#: Satisfies CHECK `at_least_one_ceiling` without expressing a ceiling.
CANONICAL_MAX_COST_MICROS: int = 0
CANONICAL_OVERAGE_POLICY: str = "REFUSE"
CANONICAL_PERIOD: str = "MONTH"


def is_entitlement_key(key: str) -> bool:
    """Exact membership. No prefix matching: `llm.` is shared with meters."""
    return key in ENTITLEMENT_KEYS


def _enum_value(value: Any) -> Any:
    """`SpendLimitPeriod.MONTH` and `"MONTH"` compare the same way here."""
    return getattr(value, "value", value)


def shape_violation(
    *,
    limit_key: str,
    period: Any,
    max_quantity: Optional[Decimal],
    max_cost_micros: Optional[int],
    overage_policy: Any,
    overage_price_tier_key: Optional[str],
    grace_quantity: Optional[Decimal],
) -> Optional[str]:
    """Why this row is not a well-formed entitlement, or None if it is.

    Returns a sentence rather than raising so the caller owns the exception
    type. `quota_service._validate` raises `QuotaTierValidationError`, which
    the publish path and the admin API already render; a second exception
    class here would need its own handler at every call site.

    Each message names the field and says what the wrong value would MEAN,
    because the person reading it is about to publish an immutable tier
    version and "invalid shape" does not tell them which belief to drop.
    """
    if not is_entitlement_key(limit_key):
        return f"'{limit_key}' is not a registered entitlement."

    prefix = f"Entitlement '{limit_key}'"

    if max_quantity is not None:
        return (
            f"{prefix} carries max_quantity={max_quantity}. An entitlement is "
            f"granted by presence and has no quantity; a quantity means this "
            f"row is being used as a meter."
        )
    if max_cost_micros != CANONICAL_MAX_COST_MICROS:
        return (
            f"{prefix} must have max_cost_micros={CANONICAL_MAX_COST_MICROS} "
            f"(got {max_cost_micros!r}). The zero only satisfies the "
            f"at_least_one_ceiling CHECK; any other value reads as a spend "
            f"ceiling on a capability."
        )
    if _enum_value(overage_policy) != CANONICAL_OVERAGE_POLICY:
        return (
            f"{prefix} must use overage_policy={CANONICAL_OVERAGE_POLICY!r} "
            f"(got {_enum_value(overage_policy)!r}). There is no overage of a "
            f"permission to warn about or bill."
        )
    if overage_price_tier_key is not None:
        return (
            f"{prefix} carries overage_price_tier_key="
            f"{overage_price_tier_key!r}. An entitlement is never priced per "
            f"unit."
        )
    if grace_quantity is not None:
        return (
            f"{prefix} carries grace_quantity={grace_quantity}. Grace is an "
            f"allowance above a quantity, and an entitlement has none."
        )
    if _enum_value(period) != CANONICAL_PERIOD:
        return (
            f"{prefix} must use period={CANONICAL_PERIOD!r} (got "
            f"{_enum_value(period)!r}). One grant, one row: the unique index "
            f"includes period, so a second period would be a second copy."
        )
    return None


def _assert_disjoint_from_meters() -> None:
    """Refuse to import if an entitlement shares a name with a meter.

    Import time, not a unit test, because the failure it prevents is silent at
    runtime: `is_limit_key` and `is_entitlement_key` would both answer True for
    the same string, and whichever branch `_validate` tests first would decide
    what the row means. That is an ordering accident, not a design.
    """
    meters = set(USAGE_EVENT_TYPES) | {TOTAL_COST_KEY}
    collisions = sorted(set(ENTITLEMENT_KEYS) & meters)
    if collisions:
        raise RuntimeError(
            "Entitlement keys collide with the usage meter vocabulary: "
            f"{', '.join(collisions)}. A key must be either a meter "
            "(app/core/usage_events.py) or an entitlement "
            "(app/core/entitlements.py), never both."
        )


_assert_disjoint_from_meters()