"""The API writes its request measurements while it runs, not only when it stops (F-328).

Every organization-scoped request is measured (latency and availability, ARCH-17) into an
in-memory recorder; the Service levels console and an Enterprise organization's priority SLO read
those measurements from `slo_observations`. The worker flushed its recorder every few passes, but
the API flushed only in its shutdown handler: while it ran, nothing it measured reached the
database, and once the recorder held 10,000 series (organizations x measures x hours) every new
measurement was dropped. The API now flushes on an interval from a background thread, off the
event loop.
"""

from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core import slo_recorder
from app.db.session import SessionLocal
from app.main import app
from app.models.organization import Organization, OrganizationStatus

pytestmark = pytest.mark.usefixtures("test_database")


def _observations(organization_id: uuid.UUID) -> int:
    with SessionLocal() as db:
        return int(db.execute(
            text("SELECT COALESCE(sum(sample_count), 0) FROM slo_observations "
                 "WHERE organization_id = :o AND slo_key = 'api.availability'"),
            {"o": organization_id},
        ).scalar_one())


def test_a_measured_request_is_written_within_the_flush_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    with SessionLocal() as db:
        organization = Organization(slug=f"slo-{uuid.uuid4().hex[:8]}", name="SLO Co.",
                                    status=OrganizationStatus.ACTIVE)
        db.add(organization)
        db.commit()
        organization_id = organization.id

    monkeypatch.setattr(slo_recorder, "FLUSH_INTERVAL_SECONDS", 0.3)
    with TestClient(app) as client:
        for _ in range(3):
            client.get(f"/api/v1/organizations/{organization_id}/usage/summary")

        deadline = time.monotonic() + 5
        written = 0
        while time.monotonic() < deadline and written < 3:
            time.sleep(0.2)
            written = _observations(organization_id)
        assert written >= 3, (
            f"{written} of 3 measured requests reached the database while the API was running"
        )
