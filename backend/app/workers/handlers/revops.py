"""ARCH50-S1:handler — the `revops.sweep` job (LIGHT profile).

Enqueued daily by scripts/sweep_revops.py (idempotency key: the UTC date). Runs app.services.revops.metrics.sweep
(contracts past their term ended and their remaining periods issued, due periods of active contracts invoiced,
unused promo reservations expired, reservations whose subscription started redeemed, last month's revenue
snapshot frozen) and prunes egress refusals past their retention (DR heartbeats prune themselves on every
beat). SQL only.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("app.workers.handlers.revops")

JOB_TYPE = "revops.sweep"


def handle_sweep(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db.session import SessionLocal
    from app.services.revops import metrics
    from app.services.sovereign import egress_policy

    with SessionLocal() as db:
        report = metrics.sweep(db)
        report["refusals_pruned"] = egress_policy.prune_refusals(db)
        db.commit()
    logger.info("revops.swept", extra=report)
    return report


__all__ = ["JOB_TYPE", "handle_sweep"]
