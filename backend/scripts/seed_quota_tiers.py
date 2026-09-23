#!/usr/bin/env python3
"""ARCH-14 Step 4 / HARDENING-MASTER — publish the four commercial tiers.

Idempotent: a tier whose latest published version already matches the table
below is skipped; a changed tier is published as the next version.

    python scripts/seed_quota_tiers.py                  # publish what changed
    python scripts/seed_quota_tiers.py --carry-forward  # + move live subscribers onto a no-worse new version
    python scripts/seed_quota_tiers.py --dry-run
    python scripts/seed_quota_tiers.py --matrix         # print the capability matrix
    python scripts/seed_quota_tiers.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.db.session import SessionLocal  # noqa: E402
from app.models.quota_tier import OveragePolicy, QuotaTier  # noqa: E402
from app.models.spend_limit import SpendLimitPeriod  # noqa: E402
from app.services import quota_service  # noqa: E402
from app.services.quota_service import TierCommercials, TierEntrySpec  # noqa: E402
from sqlalchemy import select  # noqa: E402

OVERAGE_TIER_KEY = "overage"

# ARCH-29 Tranche 2 (D-2). Presence of this entry entitles a tenant to run
# inference on the PLATFORM's provider account. Absent, `model_routing_service`
# raises `PlatformKeyNotEntitledError` instead of silently billing the
# operator, and the tenant must configure BYOK.
#
# All four seeded tiers grant it, which is correct commercially — a self-serve
# customer expects the product to work without supplying an API key, and
# consumption is still bounded by `llm.input_token` and the `*` cost ceiling.
# What changed is that the grant is now DECLARED. It is versioned, published
# through the normal path, visible on the plan card, and revocable for a tier
# by deleting one line here and publishing a new version. Before, it was the
# unconditional behaviour of six separate fallback branches.
PLATFORM_KEY = {
    "limit_key": "llm.platform_key",
    "max_cost_micros": 0,
    "overage_policy": "REFUSE",
}

# ARCH-30 Tranche 2 (D-8). Enterprise bundles both add-ons. Developer and
# Business buy them as separate gateway subscriptions ($199 and $300 a month),
# recorded in `organization_addons`, not here: a purchase has its own lifecycle
# and a published tier version cannot change.
ADDON_CUSTOM_DOMAIN = {
    "limit_key": "addon.custom_domain",
    "max_cost_micros": 0,
    "overage_policy": "REFUSE",
}
ADDON_WAREHOUSE_SYNC = {
    "limit_key": "addon.warehouse_sync",
    "max_cost_micros": 0,
    "overage_policy": "REFUSE",
}

# HARDENING-T1:D29 / HARDENING-MASTER HM-S1. The premium packaging.
#
# Four tiers, each a strict superset of the one below it. Presence of a
# `capability.*` or `addon.*` row IS the grant (`capability_gate` and
# `entitlement_service.tier_grants` both read `tier.entries[].limit_key`), so
# this table is the single source of truth for what every plan includes: the
# plan cards, the sidebar locks and every 402 on the request path read it back.
#
#   Free        core extraction and workspace chat; every premium row absent
#   Developer   + developer API keys, outgoing webhooks, custom branding and
#                 vanity domains (the custom-domain add-on, bundled)
#   Business    + three-way reconciliation, the forensic radar, custom email
#                 and analytics warehouse egress (the warehouse add-on, bundled)
#   Enterprise  + calibrated autonomy, zero-leakage redaction, clause
#                 assertions, SAML/SCIM enterprise identity, priority 99.9% SLO
#
# Change a line, run the seed: an unchanged tier is recognised and skipped,
# a changed one is published as a new immutable version.
def _capability(key: str) -> dict:
    return {"limit_key": key, "max_cost_micros": 0, "overage_policy": "REFUSE"}


DEVELOPER_FEATURES = [
    _capability("capability.developer_api"),
    _capability("capability.outgoing_webhooks"),
    _capability("capability.custom_branding"),
    ADDON_CUSTOM_DOMAIN,
]
BUSINESS_CAPABILITIES = [
    _capability("capability.reconciliation"),
    _capability("capability.anomaly_radar"),
    _capability("capability.custom_email"),
]
BUSINESS_FEATURES = [*DEVELOPER_FEATURES, *BUSINESS_CAPABILITIES, ADDON_WAREHOUSE_SYNC]
ENTERPRISE_CAPABILITIES = [
    _capability("capability.reconciliation"),
    _capability("capability.anomaly_radar"),
    _capability("capability.custom_email"),
    _capability("capability.redaction"),
    _capability("capability.semantic_assertions"),
    _capability("capability.calibrated_autonomy"),
    _capability("capability.enterprise_identity"),
    _capability("capability.priority_slo"),
]
ENTERPRISE_FEATURES = [*DEVELOPER_FEATURES, *ENTERPRISE_CAPABILITIES, ADDON_WAREHOUSE_SYNC]

# ARCH-29 Tranche 2 / HM-S1. Commercial terms.
#
# `gateway_price_id` is read from the environment, never written here: the id
# is issued by the payment gateway and differs between test and live mode.
# When the variable is unset and the previous published version of the SAME
# plan carries a price id at the same amount, that id is inherited (a gateway
# price identifies a plan, not one packaging of it; migration
# hm1_tier_price_per_key made that expressible). Otherwise a paid tier is
# refused unless --allow-unpriced says to publish it without a price.
#
# HM-S1: Enterprise is listed. $799 per seat per month, self-serve, the same
# checkout path as every other paid plan.
COMMERCIALS: dict[str, dict[str, Any]] = {
    "free": {
        "unit_amount_micros": 0,
        "currency": "USD",
        "billing_interval": "month",
        "gateway_price_id_env": None,
    },
    "developer": {
        "unit_amount_micros": 49_000_000,
        "currency": "USD",
        "billing_interval": "month",
        "gateway_price_id_env": "GATEWAY_PRICE_ID_DEVELOPER",
    },
    "business": {
        "unit_amount_micros": 299_000_000,
        "currency": "USD",
        "billing_interval": "month",
        "gateway_price_id_env": "GATEWAY_PRICE_ID_BUSINESS",
    },
    "enterprise": {
        "unit_amount_micros": 799_000_000,
        "currency": "USD",
        "billing_interval": "month",
        "gateway_price_id_env": "GATEWAY_PRICE_ID_ENTERPRISE",
    },
}

PLACEHOLDER_TIERS: dict[str, dict[str, Any]] = {
    "free": {
        "display_name": "Free",
        "entries": [
            {
                "limit_key": "*",
                "max_cost_micros": 1_000_000,
                "overage_policy": "REFUSE",
            },
            {
                "limit_key": "llm.input_token",
                "max_quantity": "100000",
                "overage_policy": "REFUSE",
                "grace_quantity": "500",
            },
            {
                "limit_key": "llm.output_token",
                "max_quantity": "25000",
                "overage_policy": "REFUSE",
                "grace_quantity": "250",
            },
            {"limit_key": "ocr.page", "max_quantity": "100", "overage_policy": "REFUSE"},
            # ARCH-29. Free previously declared no storage ceiling at all, so
            # the plan card listed tokens and OCR and was silent on documents.
            {
                "limit_key": "storage.gb_month",
                "max_quantity": "1",
                "overage_policy": "REFUSE",
            },
            PLATFORM_KEY,
        ],
    },
    "developer": {
        "display_name": "Developer",
        "entries": [
            {
                "limit_key": "*",
                "max_cost_micros": 25_000_000,
                "overage_policy": "REFUSE",
            },
            {
                "limit_key": "llm.input_token",
                "max_quantity": "2000000",
                "overage_policy": "ALLOW_AND_WARN",
                "grace_quantity": "5000",
            },
            {
                "limit_key": "llm.output_token",
                "max_quantity": "500000",
                "overage_policy": "REFUSE",
                "grace_quantity": "2000",
            },
            {
                "limit_key": "storage.gb_month",
                "max_quantity": "25",
                "overage_policy": "ALLOW_AND_BILL",
                "overage_price_tier_key": OVERAGE_TIER_KEY,
            },
            # ARCH-29. Developer sat between Free and Business, both of which
            # declared an OCR ceiling, and declared none itself — so the tier
            # the screenshots showed offered storage but appeared to offer no
            # document processing at all.
            {
                "limit_key": "ocr.page",
                "max_quantity": "5000",
                "overage_policy": "ALLOW_AND_BILL",
                "overage_price_tier_key": OVERAGE_TIER_KEY,
            },
            PLATFORM_KEY,
            *DEVELOPER_FEATURES,
        ],
    },
    "business": {
        "display_name": "Business",
        "entries": [
            {
                "limit_key": "*",
                "max_cost_micros": 500_000_000,
                "overage_policy": "REFUSE",
            },
            {
                "limit_key": "llm.output_token",
                "max_quantity": "10000000",
                "overage_policy": "REFUSE",
                "grace_quantity": "10000",
            },
            {
                "limit_key": "llm.input_token",
                "max_quantity": "50000000",
                "overage_policy": "ALLOW_AND_BILL",
                "overage_price_tier_key": OVERAGE_TIER_KEY,
                "grace_quantity": "10000",
            },
            {
                "limit_key": "storage.gb_month",
                "max_quantity": "250",
                "overage_policy": "ALLOW_AND_BILL",
                "overage_price_tier_key": OVERAGE_TIER_KEY,
            },
            {
                "limit_key": "ocr.page",
                "max_quantity": "50000",
                "overage_policy": "ALLOW_AND_BILL",
                "overage_price_tier_key": OVERAGE_TIER_KEY,
            },
            PLATFORM_KEY,
            *BUSINESS_FEATURES,
        ],
    },
    "enterprise": {
        "display_name": "Enterprise",
        "entries": [
            {
                "limit_key": "*",
                "max_cost_micros": 10_000_000_000,
                "overage_policy": "ALLOW_AND_WARN",
            },
            {
                "limit_key": "llm.input_token",
                "max_quantity": "1000000000",
                "overage_policy": "ALLOW_AND_BILL",
                "overage_price_tier_key": OVERAGE_TIER_KEY,
            },
            {
                "limit_key": "llm.output_token",
                "max_quantity": "250000000",
                "overage_policy": "ALLOW_AND_BILL",
                "overage_price_tier_key": OVERAGE_TIER_KEY,
            },
            {
                "limit_key": "storage.gb_month",
                "max_quantity": "10000",
                "overage_policy": "ALLOW_AND_BILL",
                "overage_price_tier_key": OVERAGE_TIER_KEY,
            },
            # ARCH-29. Enterprise declared no OCR ceiling while Business
            # declared 50,000 — reading the two cards side by side, the more
            # expensive plan appeared to remove a capability.
            {
                "limit_key": "ocr.page",
                "max_quantity": "1000000",
                "overage_policy": "ALLOW_AND_BILL",
                "overage_price_tier_key": OVERAGE_TIER_KEY,
            },
            PLATFORM_KEY,
            *ENTERPRISE_FEATURES,
        ],
    },
}


def _specs(rows: list[dict[str, Any]]) -> list[TierEntrySpec]:
    return [
        TierEntrySpec(
            limit_key=row["limit_key"],
            period=SpendLimitPeriod(row.get("period", "MONTH")),
            max_quantity=(
                Decimal(str(row["max_quantity"]))
                if row.get("max_quantity") is not None
                else None
            ),
            max_cost_micros=(
                int(row["max_cost_micros"])
                if row.get("max_cost_micros") is not None
                else None
            ),
            overage_policy=row.get("overage_policy", OveragePolicy.REFUSE.value),
            overage_price_tier_key=row.get("overage_price_tier_key"),
            grace_quantity=(
                Decimal(str(row["grace_quantity"]))
                if row.get("grace_quantity") is not None
                else None
            ),
            notes=row.get("notes"),
        )
        for row in rows
    ]


def _latest_published(db, key: str) -> Optional[QuotaTier]:
    return db.execute(
        select(QuotaTier)
        .where(QuotaTier.key == key, QuotaTier.published_at.is_not(None))
        .order_by(QuotaTier.version.desc())
        .limit(1)
    ).scalar_one_or_none()


def _commercials(
    key: str, *, allow_unpriced: bool, db: Any = None
) -> Optional[TierCommercials]:
    """ARCH-30 Tranche 2 (T5-F1) / HM-S1. The price terms for `key`.

    A paid tier whose gateway price id is neither in the environment nor
    inheritable from the previous version of the same plan (same amount,
    currency and interval) is a REFUSAL, not an unpriced publication, unless
    `--allow-unpriced` says otherwise. A published version is immutable:
    seeding without the id publishes a version that can never be sold.
    """
    terms = COMMERCIALS.get(key)
    if terms is None:
        return None
    env_name = terms.get("gateway_price_id_env")
    price_id = os.environ.get(env_name, "").strip() if env_name else ""
    amount = int(terms["unit_amount_micros"])
    if amount > 0 and not price_id and db is not None:
        previous = _latest_published(db, key)
        if (
            previous is not None
            and previous.gateway_price_id
            and previous.unit_amount_micros == amount
            and previous.currency == str(terms["currency"])
            and previous.billing_interval == str(terms["billing_interval"])
        ):
            price_id = previous.gateway_price_id
    if amount > 0 and not price_id:
        if allow_unpriced:
            print(f"warning: {key} published unpriced ({env_name} is not set)")
            return None
        raise ValueError(
            f"{key}: {env_name} is not set. Create the product at the gateway "
            f"({amount / 1_000_000:.2f} {terms['currency']} per seat per "
            f"{terms['billing_interval']}) and export its id, or pass "
            "--allow-unpriced to publish a tier that cannot be bought yet."
        )
    return TierCommercials(
        unit_amount_micros=amount,
        currency=str(terms["currency"]),
        billing_interval=str(terms["billing_interval"]),
        gateway_price_id=price_id or None,
    )


def _parse_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# ============================================================================
# HM-S1 — change detection, so the seed is idempotent without a --version
# ============================================================================


def _num(value: Any) -> Optional[str]:
    if value is None:
        return None
    return format(Decimal(str(value)).normalize(), "f")


def _val(value: Any) -> Any:
    return getattr(value, "value", value)


def _entry_signature(rows: Any) -> frozenset:
    return frozenset(
        (
            str(row.limit_key),
            str(_val(row.period)),
            _num(row.max_quantity),
            None if row.max_cost_micros is None else int(row.max_cost_micros),
            str(_val(row.overage_policy)),
            row.overage_price_tier_key,
            _num(row.grace_quantity),
        )
        for row in rows
    )


def _unchanged(
    latest: Optional[QuotaTier],
    display_name: str,
    specs: list[TierEntrySpec],
    terms: Optional[TierCommercials],
) -> bool:
    if latest is None or not latest.is_active or latest.effective_to is not None:
        return False
    if latest.display_name != display_name:
        return False
    if _entry_signature(latest.entries) != _entry_signature(specs):
        return False
    if terms is None:
        return latest.unit_amount_micros is None
    return (
        latest.unit_amount_micros == terms.unit_amount_micros
        and latest.currency == terms.currency
        and latest.billing_interval == terms.billing_interval
        and latest.gateway_price_id == terms.gateway_price_id
    )


def capability_matrix() -> dict[str, list[str]]:
    """Every `capability.*` / `addon.*` key each seeded tier grants.

    Read by verify_hardening_master.py, which compares it against the live
    database, the entitlements API and the console's plan cards.
    """
    return {
        key: sorted(
            {
                row["limit_key"]
                for row in payload["entries"]
                if str(row["limit_key"]).startswith(("capability.", "addon."))
            }
        )
        for key, payload in PLACEHOLDER_TIERS.items()
    }


# ============================================================================
# HM-S1 — carry live subscriptions forward onto a no-worse version
# ============================================================================


def _worse(old: QuotaTier, new: QuotaTier) -> Optional[str]:
    """Why moving a subscriber from `old` to `new` would take something away."""
    if old.unit_amount_micros is not None and (
        new.unit_amount_micros != old.unit_amount_micros
        or new.currency != old.currency
        or new.billing_interval != old.billing_interval
    ):
        return "price differs"
    after = {(e.limit_key, str(_val(e.period))): e for e in new.entries}
    for entry in old.entries:
        match = after.get((entry.limit_key, str(_val(entry.period))))
        if match is None:
            return f"drops {entry.limit_key}"
        if str(_val(match.overage_policy)) != str(_val(entry.overage_policy)):
            return f"changes the overage policy of {entry.limit_key}"
        for field in ("max_quantity", "max_cost_micros"):
            before, now = getattr(entry, field), getattr(match, field)
            if before is None and now is not None:
                return f"caps {entry.limit_key} {field}"
            if before is not None and now is not None and Decimal(str(now)) < Decimal(str(before)):
                return f"lowers {entry.limit_key} {field}"
    return None


def carry_forward(db) -> list[dict[str, Any]]:
    """Move live subscriptions onto the newest version of their own plan.

    A live subscription pins the tier version it was sold (ARCH-15 15.3), so a
    new version reaches new customers only. When the new version costs the
    same and takes nothing away, keeping existing subscribers on the old one
    withholds features they are already paying for. Anything else is skipped
    and reported: a price change or a lower ceiling is a new agreement, not a
    carry-forward.
    """
    from app.models.billing_account import BillingAccount
    from app.models.organization import Organization
    from app.models.subscription import LIVE_SUBSCRIPTION_STATUSES, Subscription

    report: list[dict[str, Any]] = []
    latest: dict[str, QuotaTier] = {}
    for tier in db.execute(
        select(QuotaTier).where(
            QuotaTier.is_active.is_(True),
            QuotaTier.published_at.is_not(None),
            QuotaTier.effective_to.is_(None),
        )
    ).scalars():
        if tier.key not in latest or tier.version > latest[tier.key].version:
            latest[tier.key] = tier

    subscriptions = db.execute(
        select(Subscription).where(Subscription.status.in_(LIVE_SUBSCRIPTION_STATUSES))
    ).scalars().all()
    for sub in subscriptions:
        target = latest.get(sub.quota_tier_key)
        if target is None or sub.quota_tier_id == target.id:
            continue
        current = db.get(QuotaTier, sub.quota_tier_id)
        problem = _worse(current, target) if current is not None else "pinned version missing"
        entry = {
            "subscription_id": str(sub.id),
            "plan": sub.quota_tier_key,
            "from_version": current.version if current is not None else None,
            "to_version": target.version,
        }
        if problem:
            report.append({**entry, "action": "skipped", "reason": problem})
            continue
        sub.quota_tier_id = target.id
        organization_id = db.execute(
            select(BillingAccount.organization_id).where(BillingAccount.id == sub.billing_account_id)
        ).scalar_one_or_none()
        if organization_id is not None:
            organization = db.get(Organization, organization_id)
            if organization is not None and organization.quota_tier_id in (None, getattr(current, "id", None)):
                organization.quota_tier_id = target.id
        report.append({**entry, "action": "moved"})
    db.flush()
    quota_service.clear_cache()
    return report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        type=int,
        default=None,
        help="explicit version number (default: next version, only for tiers that changed)",
    )
    parser.add_argument("--effective-from", type=str, default=None)
    parser.add_argument("--from-json", type=Path, default=None)
    parser.add_argument("--only", type=str, default=None)
    parser.add_argument("--assign", type=str, default=None)
    parser.add_argument("--tier", type=str, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--allow-unpriced",
        action="store_true",
        help="publish a paid tier without a gateway price id (it cannot be sold)",
    )
    parser.add_argument(
        "--carry-forward",
        action="store_true",
        help="move live subscriptions onto the newest version of their plan when it costs the same and takes nothing away",
    )
    parser.add_argument("--matrix", action="store_true", help="print the capability matrix and exit")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    if args.matrix:
        print(json.dumps(capability_matrix(), indent=2))
        return 0

    if args.assign:
        if not args.tier:
            parser.error("--assign requires --tier")
        db = SessionLocal()
        try:
            tier = quota_service.assign_tier(
                db,
                organization_id=uuid.UUID(args.assign),
                tier_key=args.tier,
            )
            label = f"{tier.key}/v{tier.version}"
            db.commit()
            print(f"organization {args.assign} -> {label}")
            return 0
        except Exception as exc:
            db.rollback()
            print(f"Error assigning tier: {exc}")
            return 1
        finally:
            db.close()

    source: dict[str, Any] = (
        json.loads(args.from_json.read_text(encoding="utf-8"))
        if args.from_json
        else PLACEHOLDER_TIERS
    )
    if args.only:
        source = {args.only: source[args.only]}

    effective_from = (
        _parse_instant(args.effective_from)
        if args.effective_from
        else datetime.now(timezone.utc)
    )

    prepared = {
        key: (payload["display_name"], _specs(payload["entries"]))
        for key, payload in source.items()
    }

    db = SessionLocal()
    published: list[str] = []
    try:
        try:
            commercials = {
                key: _commercials(key, allow_unpriced=args.allow_unpriced, db=db)
                for key in source
            }
        except ValueError as exc:
            print(f"Refusing to publish: {exc}")
            return 2

        highest = db.execute(select(QuotaTier.version).order_by(QuotaTier.version.desc()).limit(1)).scalar_one_or_none() or 0
        target_version = args.version if args.version is not None else highest + 1

        plan: dict[str, str] = {}
        for key, (display_name, specs) in prepared.items():
            if args.version is None and _unchanged(
                _latest_published(db, key), display_name, specs, commercials[key]
            ):
                plan[key] = "current"
            else:
                plan[key] = "publish"

        for key, terms in commercials.items():
            label = (
                f"{terms.unit_amount_micros / 1_000_000:.2f} {terms.currency}/"
                f"{terms.billing_interval} price={terms.gateway_price_id or '-'}"
                if terms
                else "unpriced"
            )
            action = "unchanged, skipped" if plan[key] == "current" else f"publish v{target_version}"
            print(f"{key}: {label} ({action})")

        if args.dry_run:
            print(f"effective_from: {effective_from.isoformat()}")
            print("\ndry-run: nothing written.")
            return 0

        for key, (display_name, specs) in prepared.items():
            if plan[key] == "current":
                latest = _latest_published(db, key)
                published.append(f"{key}/v{latest.version} (current)")
                continue
            existing = db.execute(
                select(QuotaTier).where(
                    QuotaTier.key == key,
                    QuotaTier.version == target_version,
                )
            ).scalar_one_or_none()
            if existing is not None:
                published.append(f"{existing.key}/v{existing.version} (already exists)")
                continue

            tier = quota_service.publish_tier(
                db,
                key=key,
                display_name=display_name,
                version=target_version,
                effective_from=effective_from,
                entries=specs,
                commercials=commercials[key],
            )
            published.append(f"{tier.key}/v{tier.version}")
        db.flush()

        moved: list[dict[str, Any]] = []
        if args.carry_forward:
            moved = carry_forward(db)
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"Error publishing quota tiers: {exc}")
        return 1
    finally:
        db.close()

    if args.as_json:
        print(json.dumps({"status": "ok", "tiers": published, "carried_forward": moved}, indent=2))
    else:
        print(f"Quota tiers: {', '.join(published)}")
        if args.carry_forward:
            done = [m for m in moved if m["action"] == "moved"]
            skipped = [m for m in moved if m["action"] == "skipped"]
            print(f"Carry-forward: {len(done)} subscription(s) moved, {len(skipped)} skipped")
            for m in skipped:
                print(f"  skipped {m['subscription_id']} ({m['plan']} v{m['from_version']}): {m['reason']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
