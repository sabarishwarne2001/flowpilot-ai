"""ARCH-14 Step 4 — publish quota tiers.

    python -m scripts.seed_quota_tiers --dry-run
    python -m scripts.seed_quota_tiers --version 1 --effective-from 2026-09-01T00:00:00Z
    python -m scripts.seed_quota_tiers --assign <org-uuid> --tier business
"""

from __future__ import annotations

import argparse
import json
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
from app.models.quota_tier import OveragePolicy  # noqa: E402
from app.models.spend_limit import SpendLimitPeriod  # noqa: E402
from app.services import quota_service  # noqa: E402
from app.services.quota_service import TierEntrySpec  # noqa: E402

OVERAGE_TIER_KEY = "overage"

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
                "max_quantity": "500",
                "overage_policy": "ALLOW_AND_BILL",
                "overage_price_tier_key": OVERAGE_TIER_KEY,
            },
            {
                "limit_key": "ocr.page",
                "max_quantity": "50000",
                "overage_policy": "ALLOW_AND_BILL",
                "overage_price_tier_key": OVERAGE_TIER_KEY,
            },
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


def _parse_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _print_tier(key: str, display_name: str, specs: list[TierEntrySpec]) -> None:
    print(f"\n{key}  ({display_name})")
    for spec in sorted(specs, key=lambda s: s.limit_key):
        ceiling = (
            f"qty<={spec.max_quantity}"
            if spec.max_quantity is not None
            else f"cost<={spec.max_cost_micros}µ"
        )
        grace = f" +{spec.grace_quantity} grace" if spec.grace_quantity else ""
        price = (
            f" @{spec.overage_price_tier_key}"
            if spec.overage_price_tier_key
            else ""
        )
        print(
            f"  {spec.limit_key:<22} {spec.period.value:<6} {ceiling:<26}"
            f"{spec.overage_policy}{price}{grace}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", type=int, default=1)
    parser.add_argument(
        "--effective-from",
        type=str,
        default=None,
    )
    parser.add_argument("--from-json", type=Path, default=None)
    parser.add_argument(
        "--only", type=str, default=None,
    )
    parser.add_argument(
        "--assign",
        type=str,
        default=None,
    )
    parser.add_argument("--tier", type=str, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

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
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        print(f"organization {args.assign} -> {label}")
        return 0

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

    if args.from_json is None:
        print(
            "WARNING: publishing PLACEHOLDER ceilings from this script's "
            "source. Pass --from-json with the agreed plan limits.",
            file=sys.stderr,
        )

    print(f"version:        {args.version}")
    print(f"effective_from: {effective_from.isoformat()}")
    prepared = {
        key: (payload["display_name"], _specs(payload["entries"]))
        for key, payload in source.items()
    }
    for key, (display_name, specs) in prepared.items():
        _print_tier(key, display_name, specs)

    if args.dry_run:
        print("\ndry-run: nothing written.")
        return 0

    db = SessionLocal()
    published: list[str] = []
    try:
        for key, (display_name, specs) in prepared.items():
            tier = quota_service.publish_tier(
                db,
                key=key,
                display_name=display_name,
                version=args.version,
                effective_from=effective_from,
                entries=specs,
            )
            published.append(f"{tier.key}/v{tier.version}")
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(f"\npublished: {', '.join(published)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())