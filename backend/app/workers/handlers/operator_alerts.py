"""`ops.alerts` — scan for what the deployment's owner must hear about, and email it (§C.4).

Every five minutes on the LIGHT profile (and at once after a duplicate gateway subscription):
dead jobs, dead gateway events and bursts of server errors are recorded as `operator_alerts`,
then every alert with news is emailed to `OPERATOR_ALERT_EMAILS`. A platform job: it belongs to
no organization, so the tenant gate never skips it.
"""

from __future__ import annotations

from typing import Any

JOB_TYPE = "ops.alerts"


def handle_operator_alerts(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db.session import SessionLocal
    from app.services import operator_alert_service

    with SessionLocal() as db:
        refreshed = operator_alert_service.scan(db)
        db.commit()
    with SessionLocal() as db:
        sent = operator_alert_service.send_due(db)
        db.commit()
    return {"outcome": "COMPLETED", "alerts_refreshed": refreshed, "alerts_emailed": sent}
