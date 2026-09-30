"""Put a test organization on a real commercial tier.

The tiers are the ones `scripts/seed_quota_tiers.py` publishes, loaded from
that file so the tests cannot drift from what production seeds. Nothing here is
a mock: `quota_service.resolve_tier` and every capability gate read exactly
these rows.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy.orm import Session

from app.models.organization import Organization
from app.models.price_book import PriceBook
from app.services import pricing_service, quota_service

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
_SEED_PATH = _SCRIPTS / "seed_quota_tiers.py"
_PRICE_BOOK_PATH = _SCRIPTS / "seed_price_book.py"
_seed_module: ModuleType | None = None
_price_book_module: ModuleType | None = None

PLAN_KEYS = ("free", "developer", "business", "enterprise")


def _seed() -> ModuleType:
    global _seed_module
    if _seed_module is None:
        spec = importlib.util.spec_from_file_location("seed_quota_tiers_for_tests", _SEED_PATH)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _seed_module = module
    return _seed_module


def _price_book_seed() -> ModuleType:
    global _price_book_module
    if _price_book_module is None:
        spec = importlib.util.spec_from_file_location("seed_price_book_for_tests", _PRICE_BOOK_PATH)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _price_book_module = module
    return _price_book_module


def _ensure_price_book(db: Session) -> None:
    """Tiers that bill overage refuse to publish without a price book in force."""
    if db.query(PriceBook).first() is not None:
        return
    pricing_service.publish(
        db,
        version=1,
        effective_from=datetime.now(timezone.utc) - timedelta(days=2),
        entries=_price_book_seed()._load_entries(None),  # noqa: SLF001 - the seed's own loader
        currency="USD",
        notes="test price book",
        close_predecessor=True,
    )
    db.flush()
    pricing_service.clear_cache()


def put_on_plan(db: Session, organization: Organization, plan_key: str) -> Any:
    """Publish tier `plan_key` (if it is not yet) and point `organization` at it."""
    _ensure_price_book(db)
    seed = _seed()
    tier = seed._latest_published(db, plan_key)  # noqa: SLF001 - the seed's own lookup
    if tier is None:
        definition = seed.PLACEHOLDER_TIERS[plan_key]
        tier = quota_service.publish_tier(
            db,
            key=plan_key,
            display_name=definition["display_name"],
            version=1,
            effective_from=datetime.now(timezone.utc) - timedelta(days=1),
            entries=seed._specs(definition["entries"]),  # noqa: SLF001
        )
        db.flush()
    organization.quota_tier_id = tier.id
    db.add(organization)
    db.commit()
    quota_service.clear_cache()
    return tier
