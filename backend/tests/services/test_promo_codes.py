"""Promo codes give exactly the discount they promise, to whom they promise it (campaign session 1, F).

    pytest tests/services/test_promo_codes.py -q

The promo service (`app/services/revops/promos.py`) had no tests. These prove its rules on
a real database:

  * a percentage or amount comes off the list price and never more than the price;
  * an expired, inactive, wrong-plan or wrong-interval code is refused with its reason;
  * an organization uses a code once; a "first subscription only" code is refused to an
    organization that has had a subscription;
  * a capped code is never over-allocated: five checkouts racing for the last use, each
    in its own transaction and thread, produce exactly one reservation;
  * a reservation that never became a subscription gives its use back when released.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.organization import Organization
from app.services.revops import promos
from app.services.revops.service import RevOpsError
from tests.api.test_seat_purchase_api import _on_business, fake_gateway  # noqa: F401 - fixture
from tests.conftest import TestSessionLocal

BUSINESS_MICROS = 299_000_000


def _code(db: Session, **spec) -> str:
    code = f"S1{uuid.uuid4().hex[:8]}".upper()
    promos.create_code(db, spec={"code": code, **spec}, actor_id=None)
    db.commit()
    return code


def _org(db: Session) -> uuid.UUID:
    org = Organization(name=f"Promo {uuid.uuid4().hex[:6]}", slug=f"promo-{uuid.uuid4().hex[:10]}")
    db.add(org)
    db.commit()
    return org.id


def _quote(db: Session, org: uuid.UUID, code: str, *, tier: str = "business", interval: str = "month",
           currency: str = "USD", amount: int = BUSINESS_MICROS) -> dict:
    return promos.quote(db, organization_id=org, code=code, tier_key=tier, interval=interval, currency=currency,
                        list_amount_micros=amount)


def _reserve(db: Session, org: uuid.UUID, code: str) -> dict:
    return promos.reserve(db, organization_id=org, code=code, tier_key="business", interval="month",
                          currency="USD", list_amount_micros=BUSINESS_MICROS, for_gateway=True)


def test_the_discount_is_what_the_code_says_and_never_more_than_the_price(db_session: Session, tenant) -> None:
    org = tenant.organization.id
    twenty = _code(db_session, percent_off=20, gateway_coupon_id="coupon_20")
    assert _quote(db_session, org, twenty)["final_amount_micros"] == 239_200_000
    huge = _code(db_session, amount_off=1_000_000, currency="USD", gateway_coupon_id="coupon_big")
    quoted = _quote(db_session, org, huge)
    assert (quoted["discount_micros"], quoted["final_amount_micros"]) == (BUSINESS_MICROS, 0)


@pytest.mark.parametrize(("spec", "reason"), [
    ({"percent_off": 10, "is_active": False}, "PROMO_INACTIVE"),
    ({"percent_off": 10, "redeem_by": datetime.now(timezone.utc) - timedelta(days=1)}, "PROMO_EXPIRED"),
    ({"percent_off": 10, "applies_to_tiers": ["enterprise"]}, "PROMO_NOT_APPLICABLE"),
    ({"percent_off": 10, "applies_to_intervals": ["year"]}, "PROMO_NOT_APPLICABLE"),
    ({"amount_off": 50, "currency": "INR"}, "PROMO_CURRENCY_MISMATCH"),
])
def test_a_code_that_does_not_apply_is_refused_with_its_reason(db_session: Session, tenant, spec, reason) -> None:
    inactive = spec.pop("is_active", True)
    code = _code(db_session, **spec)
    if not inactive:
        promos.set_active(db_session, promo_id=promos._row(db_session, code)["id"], active=False)  # noqa: SLF001
        db_session.commit()
    with pytest.raises(RevOpsError) as refused:
        _quote(db_session, tenant.organization.id, code)
    assert refused.value.code == reason


def test_an_organization_uses_a_code_once(db_session: Session, tenant) -> None:
    code = _code(db_session, percent_off=10, gateway_coupon_id="coupon_10")
    _reserve(db_session, tenant.organization.id, code)
    db_session.commit()
    with pytest.raises(RevOpsError) as again:
        _reserve(db_session, tenant.organization.id, code)
    assert again.value.code == "PROMO_ALREADY_USED"


def test_a_capped_code_is_never_over_allocated_under_a_race(db_session: Session) -> None:
    code = _code(db_session, percent_off=50, max_redemptions=1, gateway_coupon_id="coupon_half")
    orgs = [_org(db_session) for _ in range(5)]
    start = threading.Barrier(len(orgs))
    outcomes: list[str] = []
    lock = threading.Lock()

    def checkout(org: uuid.UUID) -> None:
        session = TestSessionLocal()
        try:
            start.wait()
            _reserve(session, org, code)
            session.commit()
            result = "RESERVED"
        except RevOpsError as exc:
            session.rollback()
            result = exc.code
        finally:
            session.close()
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=checkout, args=(org,)) for org in orgs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(outcomes) == ["PROMO_EXHAUSTED"] * 4 + ["RESERVED"], outcomes


def test_a_released_reservation_gives_its_use_back(db_session: Session) -> None:
    code = _code(db_session, percent_off=50, max_redemptions=1, gateway_coupon_id="coupon_back")
    first, second = _org(db_session), _org(db_session)
    held = _reserve(db_session, first, code)
    db_session.commit()
    with pytest.raises(RevOpsError):
        _reserve(db_session, second, code)
    db_session.rollback()
    assert promos.release(db_session, redemption_id=held["redemption_id"]) is True
    db_session.commit()
    assert _reserve(db_session, second, code)["discount_micros"] == BUSINESS_MICROS // 2


def test_a_first_subscription_code_is_refused_to_an_organization_that_has_subscribed(
    db_session: Session, tenant, fake_gateway  # noqa: F811
) -> None:
    code = _code(db_session, percent_off=30, first_subscription_only=True, gateway_coupon_id="coupon_first")
    assert _quote(db_session, tenant.organization.id, code)["valid"] is True
    _on_business(db_session, tenant, fake_gateway, seats=5)
    with pytest.raises(RevOpsError) as refused:
        _quote(db_session, tenant.organization.id, code)
    assert refused.value.code == "PROMO_FIRST_SUBSCRIPTION_ONLY"
