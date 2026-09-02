"""ARCH-16 — scheduled job handlers."""

from __future__ import annotations

import logging
from app.services.identity import domain_service, saml_gateway

logger = logging.getLogger(__name__)

JOB_RECHECK_DOMAINS = "identity.recheck_domains"
JOB_PURGE_ASSERTIONS = "identity.purge_assertion_payloads"
JOB_SWEEP_REPLAY_GUARD = "identity.sweep_replay_guard"
JOB_SWEEP_AUTH_REQUESTS = "identity.sweep_auth_requests"


def handle_recheck_domains(db, payload: dict | None = None) -> dict:
    limit = int((payload or {}).get("limit", 200))
    outcomes = domain_service.recheck_domains(db, limit=limit)
    summary = {
        "checked": len(outcomes),
        "resolver_failures": sum(1 for o in outcomes if o.resolver_failed),
        "transitions": [
            {"domain": o.domain, "from": o.previous_status, "to": o.new_status}
            for o in outcomes if o.previous_status != o.new_status
        ],
    }
    if summary["resolver_failures"]:
        logger.warning("ARCH-16: %d domain checks could not reach a resolver",
                       summary["resolver_failures"])
    return summary


def handle_purge_assertion_payloads(db, payload: dict | None = None) -> dict:
    purged = domain_service.purge_expired_assertion_payloads(
        db, limit=int((payload or {}).get("limit", 1000)))
    return {"purged": purged}


def handle_sweep_replay_guard(db, payload: dict | None = None) -> dict:
    removed = saml_gateway.sweep_replay_guard(
        db, grace_hours=int((payload or {}).get("grace_hours", 24)))
    return {"removed": removed}


def handle_sweep_auth_requests(db, payload: dict | None = None) -> dict:
    from sqlalchemy import text

    removed = db.execute(
        text("DELETE FROM sso_auth_requests WHERE expires_at < now() - interval '1 day'")
    ).rowcount or 0
    db.commit()
    return {"removed": removed}


HANDLERS = {
    JOB_RECHECK_DOMAINS: handle_recheck_domains,
    JOB_PURGE_ASSERTIONS: handle_purge_assertion_payloads,
    JOB_SWEEP_REPLAY_GUARD: handle_sweep_replay_guard,
    JOB_SWEEP_AUTH_REQUESTS: handle_sweep_auth_requests,
}
