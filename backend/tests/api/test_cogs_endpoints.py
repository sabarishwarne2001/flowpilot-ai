"""Gate 18.2 — the superadmin gate and the reconciliation workflow over HTTP.

The gating tests come first and they are not a formality. Every endpoint here
reads across tenant boundaries: a hole in this router does not leak one
organization's data to another, it leaks every organization's cost structure
and margin to whoever finds it. A1 walks the whole router rather than testing
one representative route, because the failure mode is a route added in six
months whose author forgot the dependency — and a test that checks one route
by name will never catch that.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import security
from app.main import app
from app.models.organization import Organization, OrganizationStatus
from app.models.user import User
from app.services import pricing_service, usage_service
from app.services import supplier_reconciliation_service as recon
from app.services.pricing_service import PriceSpec

pytestmark = pytest.mark.usefixtures("test_database")

API = "/api/v1/admin/cogs"

PRICE_MICROS = Decimal("1.000000000")
COST_MICROS = Decimal("0.400000000")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _no_price_cache(monkeypatch):
    monkeypatch.setattr(
        pricing_service.settings, "PRICE_BOOK_CACHE_TTL_SECONDS", 0.0, raising=False
    )
    pricing_service.clear_cache()
    yield
    pricing_service.clear_cache()


def _user(db: Session, *, email: str, superuser: bool, verified: bool = True) -> User:
    record = User(
        email=email,
        hashed_password=security.get_password_hash("test-password"),
        is_active=True,
        is_superuser=superuser,
        email_verified_at=datetime.now(timezone.utc) if verified else None,
    )
    db.add(record)
    db.flush()
    return record


@pytest.fixture()
def superadmin_token(db_session: Session) -> str:
    user = _user(
        db_session, email=f"root-{uuid.uuid4().hex[:8]}@flowpilot.ai", superuser=True
    )
    db_session.commit()
    return security.create_access_token(subject=user.id)


@pytest.fixture()
def member_token(db_session: Session) -> str:
    user = _user(
        db_session, email=f"member-{uuid.uuid4().hex[:8]}@acme.com", superuser=False
    )
    db_session.commit()
    return security.create_access_token(subject=user.id)


@pytest.fixture()
def unverified_superadmin_token(db_session: Session) -> str:
    user = _user(
        db_session,
        email=f"pending-{uuid.uuid4().hex[:8]}@flowpilot.ai",
        superuser=True,
        verified=False,
    )
    db_session.commit()
    return security.create_access_token(subject=user.id)


@pytest.fixture()
def priced_usage(db_session: Session) -> Organization:
    """One org, one published book with cost basis, two settled rows."""
    org = Organization(
        slug=f"api-cogs-{uuid.uuid4().hex[:8]}",
        name="API COGS Co.",
        status=OrganizationStatus.ACTIVE,
    )
    db_session.add(org)
    db_session.flush()

    book = pricing_service.publish(
        db_session,
        version=1,
        effective_from=datetime.now(timezone.utc) - timedelta(days=90),
        entries=[
            PriceSpec(
                event_type="llm.input_token",
                provider="groq",
                unit_price_micros=PRICE_MICROS,
                cost_basis_micros=COST_MICROS,
                cost_basis_source="SUPPLIER_RATE_CARD",
            ),
            PriceSpec(
                event_type="llm.output_token",
                provider="groq",
                unit_price_micros=PRICE_MICROS,
                cost_basis_micros=COST_MICROS,
                cost_basis_source="SUPPLIER_RATE_CARD",
            ),
        ],
    )

    for cost_basis, source in ((400, "SUPPLIER_RATE_CARD"), (None, None)):
        usage_service.record_usage(
            db_session,
            organization_id=org.id,
            event_type="llm.input_token",
            quantity=Decimal(1000),
            cost_micros=1000,
            price_book_id=book.id,
            unit_price_micros=PRICE_MICROS,
            cost_basis_micros=cost_basis,
            cost_basis_source=source,
            provider="groq",
            idempotency_key=f"api-{uuid.uuid4().hex}",
            require_active_transaction=False,
        )

    db_session.commit()
    return org


# =========================================================================
# A1-A4 — the gate
# =========================================================================


def _cogs_routes() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if "/admin/cogs" not in path:
            continue
        for method in sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}):
            out.append((method, path))
    return out


def test_a1_every_cogs_route_requires_superadmin(
    client: TestClient, member_token: str
):
    """Walk the router. Not one representative route — all of them.

    A non-superadmin gets 404 rather than 403 by design: a 403 confirms that a
    cross-tenant margin surface exists and that the caller is merely on the
    wrong side of it.
    """
    routes = _cogs_routes()
    assert routes, "No /admin/cogs routes are mounted — the router is not wired."

    failures: list[str] = []
    for method, path in routes:
        concrete = path
        for placeholder in ("{supplier_invoice_id}", "{reconciliation_id}"):
            concrete = concrete.replace(placeholder, str(uuid.uuid4()))

        response = client.request(
            method, concrete, headers=_auth(member_token), json={}
        )
        if response.status_code != 404:
            failures.append(f"{method} {path} -> {response.status_code}")

    assert not failures, (
        "These platform routes did not refuse a regular user:\n  "
        + "\n  ".join(failures)
    )


def test_a2_anonymous_access_is_refused(client: TestClient):
    response = client.get(f"{API}/margins/summary")
    assert response.status_code in (401, 403)


def test_a3_an_unverified_superadmin_is_refused(
    client: TestClient, unverified_superadmin_token: str
):
    """require_superadmin chains off get_verified_user, so ARCH-03's email
    verification gate applies to the platform surface too."""
    response = client.get(
        f"{API}/margins/summary", headers=_auth(unverified_superadmin_token)
    )
    assert response.status_code == 403


def test_a4_a_superadmin_gets_through(client: TestClient, superadmin_token: str):
    response = client.get(
        f"{API}/margins/summary", headers=_auth(superadmin_token)
    )
    assert response.status_code == 200
    body = response.json()
    assert "figures" in body
    assert "unknown_cost_share" in body["figures"]


# =========================================================================
# A5-A7 — margins
# =========================================================================


def test_a5_summary_reports_margin_and_the_unknown_share(
    client: TestClient, superadmin_token: str, priced_usage: Organization
):
    response = client.get(
        f"{API}/margins/summary", headers=_auth(superadmin_token)
    )
    assert response.status_code == 200
    figures = response.json()["figures"]

    assert figures["revenue_micros"] == 2000
    assert figures["attributed_revenue_micros"] == 1000
    assert figures["cost_basis_micros"] == 400
    assert figures["gross_margin_micros"] == 600
    assert figures["gross_margin_ratio"] == pytest.approx(0.6)

    assert figures["unknown_cost_event_count"] == 1
    assert figures["unknown_cost_share"] == pytest.approx(0.5)
    assert figures["is_trustworthy"] is False, (
        "Half the revenue has no cost. The API must say the figure is not "
        "quotable rather than let the dashboard print 60% in large type."
    )


def test_a6_tenant_ranking_returns_worst_first_and_caps_limit(
    client: TestClient, superadmin_token: str, priced_usage: Organization
):
    response = client.get(
        f"{API}/margins/tenants",
        headers=_auth(superadmin_token),
        params={"order": "MARGIN_ASC", "limit": 10_000},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["order"] == "MARGIN_ASC"
    assert len(body["entries"]) >= 1

    entry = next(
        e for e in body["entries"]
        if e["organization_id"] == str(priced_usage.id)
    )
    assert entry["organization_name"] == "API COGS Co."
    assert entry["figures"]["gross_margin_micros"] == 600

    bad = client.get(
        f"{API}/margins/tenants",
        headers=_auth(superadmin_token),
        params={"order": "NOT_AN_ORDER"},
    )
    assert bad.status_code == 422


def test_a7_rate_card_exposes_cost_coverage(
    client: TestClient, superadmin_token: str, priced_usage: Organization
):
    response = client.get(f"{API}/rate-card", headers=_auth(superadmin_token))
    assert response.status_code == 200
    body = response.json()

    assert body["price_book_version"] == 1
    assert body["entry_count"] == 2
    assert body["with_cost_basis"] == 2
    assert body["coverage_ratio"] == pytest.approx(1.0)

    entry = body["entries"][0]
    assert entry["cost_basis_source"] == "SUPPLIER_RATE_CARD"
    # Serialised as strings: nine decimal places do not survive a float.
    assert entry["unit_price_micros"] == "1.000000000"
    assert entry["cost_basis_micros"] == "0.400000000"
    assert entry["unit_margin_micros"] == "0.600000000"


def test_a8_an_inverted_window_is_refused(
    client: TestClient, superadmin_token: str
):
    now = datetime.now(timezone.utc)
    response = client.get(
        f"{API}/margins/summary",
        headers=_auth(superadmin_token),
        params={
            "period_start": now.isoformat(),
            "period_end": (now - timedelta(days=1)).isoformat(),
        },
    )
    assert response.status_code == 400


# =========================================================================
# A9-A12 — the supplier invoice workflow
# =========================================================================


def test_a9_ingest_reconcile_accept_round_trip(
    client: TestClient, superadmin_token: str, priced_usage: Organization
):
    period_end = (datetime.now(timezone.utc) - timedelta(days=10)).date()
    period_start = period_end - timedelta(days=30)

    created = client.post(
        f"{API}/supplier-invoices",
        headers=_auth(superadmin_token),
        json={
            "provider": "GROQ",
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "invoiced_total_micros": 900_000,
            "currency": "USD",
            "invoice_reference": "INV-2026-07",
        },
    )
    assert created.status_code == 201
    invoice = created.json()
    assert invoice["provider"] == "groq", "Provider must be normalised on ingest."
    assert invoice["latest_reconciliation"] is None

    reconciled = client.post(
        f"{API}/supplier-invoices/{invoice['id']}/reconcile",
        headers=_auth(superadmin_token),
        json={"note": "July close"},
    )
    assert reconciled.status_code == 201
    result = reconciled.json()

    # No usage inside that historical window, so the modelled total is zero
    # and the ratio must be undefined rather than a flattering 0.0.
    assert result["modelled_total_micros"] == 0
    assert result["variance_ratio"] is None
    assert result["status"] == "INVESTIGATE"

    missing_note = client.post(
        f"{API}/reconciliations/{result['id']}/accept",
        headers=_auth(superadmin_token),
        json={"note": ""},
    )
    assert missing_note.status_code == 422

    accepted = client.post(
        f"{API}/reconciliations/{result['id']}/accept",
        headers=_auth(superadmin_token),
        json={"note": "Prepaid credits drawdown, not usage-linked."},
    )
    assert accepted.status_code == 201
    assert accepted.json()["status"] == "ACCEPTED"
    assert accepted.json()["id"] != result["id"]

    history = client.get(
        f"{API}/supplier-invoices/{invoice['id']}/reconciliations",
        headers=_auth(superadmin_token),
    )
    assert history.status_code == 200
    assert [r["status"] for r in history.json()] == ["ACCEPTED", "INVESTIGATE"]


def test_a10_a_duplicate_invoice_period_returns_409(
    client: TestClient, superadmin_token: str
):
    period_end = (datetime.now(timezone.utc) - timedelta(days=15)).date()
    payload = {
        "provider": "openai",
        "period_start": (period_end - timedelta(days=30)).isoformat(),
        "period_end": period_end.isoformat(),
        "invoiced_total_micros": 1234,
    }

    first = client.post(
        f"{API}/supplier-invoices", headers=_auth(superadmin_token), json=payload
    )
    assert first.status_code == 201

    second = client.post(
        f"{API}/supplier-invoices", headers=_auth(superadmin_token), json=payload
    )
    assert second.status_code == 409


def test_a11_an_open_period_returns_409_not_400(
    client: TestClient, superadmin_token: str, db_session: Session
):
    """The request is well-formed and will succeed unchanged once the period
    closes. That is a state conflict, and whatever retries it needs to know
    the difference."""
    today = datetime.now(timezone.utc).date()
    invoice = recon.ingest_invoice(
        db_session,
        provider="anthropic",
        period_start=today - timedelta(days=2),
        period_end=today,
        invoiced_total_micros=42,
    )
    db_session.commit()

    blocked = client.post(
        f"{API}/supplier-invoices/{invoice.id}/reconcile",
        headers=_auth(superadmin_token),
        json={},
    )
    assert blocked.status_code == 409

    forced = client.post(
        f"{API}/supplier-invoices/{invoice.id}/reconcile",
        headers=_auth(superadmin_token),
        json={"force": True},
    )
    assert forced.status_code == 201


def test_a12_reconciling_an_unknown_invoice_returns_404(
    client: TestClient, superadmin_token: str
):
    response = client.post(
        f"{API}/supplier-invoices/{uuid.uuid4()}/reconcile",
        headers=_auth(superadmin_token),
        json={},
    )
    assert response.status_code == 404


def test_a13_provider_costs_report_unknown_rows(
    client: TestClient, superadmin_token: str, priced_usage: Organization
):
    response = client.get(
        f"{API}/margins/providers", headers=_auth(superadmin_token)
    )
    assert response.status_code == 200

    groq = next(
        e for e in response.json()["entries"] if e["provider"] == "groq"
    )
    assert groq["cost_basis_micros"] == 400
    assert groq["revenue_micros"] == 2000
    assert groq["unknown_cost_event_count"] == 1
