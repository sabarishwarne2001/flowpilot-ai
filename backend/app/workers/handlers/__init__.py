"""Job handler registration across ARCH-10 through ARCH-16."""

from __future__ import annotations

import logging
from typing import Any

from app.services import job_service

logger = logging.getLogger("app.workers.handlers")

ARCH10_JOB_TYPES: frozenset[str] = frozenset(
    {"document.extract", "document.enrich", "storage.sample"}
)
ARCH11_JOB_TYPES: frozenset[str] = frozenset({"knowledge.reindex"})
ARCH12_JOB_TYPES: frozenset[str] = frozenset({"notification.deliver"})
ARCH14_JOB_TYPES: frozenset[str] = frozenset(
    {"usage.rollup", "usage.seal", "usage.reconcile"}
)
ARCH13_JOB_TYPES: frozenset[str] = frozenset(
    {"automation.execute", "document.verify"}
)
ARCH15_JOB_TYPES: frozenset[str] = frozenset(
    {
        "billing.reconcile",
        "billing.seat_sync",
        "billing.seat_drift",
        "billing.assemble_invoice",
        "billing.dunning_sweep",
        "billing.addon_grace_sweep",
    }
)
ARCH16_JOB_TYPES: frozenset[str] = frozenset(
    {
        "identity.recheck_domains",
        "identity.purge_assertion_payloads",
        "identity.sweep_replay_guard",
        "identity.sweep_auth_requests",
    }
)
ARCH25_JOB_TYPES: frozenset[str] = frozenset(
    {"domain.verify_dns", "tls.renew_sweep"}
)
ARCH26_JOB_TYPES: frozenset[str] = frozenset(
    {"analytics.export_sync", "analytics.warehouse_push"}
)
ARCH27_JOB_TYPES: frozenset[str] = frozenset(
    {"partner.rev_share_compute", "partner.rev_share_seal"}
)
ARCH31_JOB_TYPES: frozenset[str] = frozenset({"procurement.score"})
ARCH32_JOB_TYPES: frozenset[str] = frozenset(
    {"redaction.detect", "redaction.apply"}
)
ARCH34_JOB_TYPES: frozenset[str] = frozenset(
    {"anomaly.scan_document", "anomaly.nightly"}
)

#: Every job type this package claims to register, by phase.
#:
#: ARCH-27 carried-forward resolution 2. Before this, ARCH16_JOB_TYPES,
#: ARCH25_JOB_TYPES and ARCH26_JOB_TYPES were module-level exports with zero
#: consumers — the recurring "orphaned guard" defect class in this codebase:
#: correct declarations that no code path reads, invisible to linters, and
#: therefore free to drift from the registry they claim to describe.
#:
#: `register_all()` now asserts this union equals `_HANDLERS.keys()`, so a
#: phase constant that falls out of step with the handler table fails loudly
#: at import instead of silently documenting a lie.
ALL_PHASE_JOB_TYPES: frozenset[str] = (
    ARCH10_JOB_TYPES
    | ARCH11_JOB_TYPES
    | ARCH12_JOB_TYPES
    | ARCH13_JOB_TYPES
    | ARCH14_JOB_TYPES
    | ARCH15_JOB_TYPES
    | ARCH16_JOB_TYPES
    | ARCH25_JOB_TYPES
    | ARCH26_JOB_TYPES
    | ARCH27_JOB_TYPES
    | ARCH31_JOB_TYPES
    | ARCH32_JOB_TYPES
    | ARCH34_JOB_TYPES
)


def _document_extract(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.ocr import handle_document_extract
    return handle_document_extract(payload)


def _document_enrich(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.enrich import handle_document_enrich
    return handle_document_enrich(payload)


def _storage_sample(payload: dict[str, Any]) -> dict[str, Any]:
    from app.services.storage_sampler_service import handle_storage_sample
    return handle_storage_sample(payload)


def _knowledge_reindex(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.knowledge_reindex import handle_knowledge_reindex
    return handle_knowledge_reindex(payload)


def _notification_deliver(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.notify import handle_notification_deliver
    return handle_notification_deliver(payload)


def _usage_rollup(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.rollup import handle_usage_rollup
    return handle_usage_rollup(payload)


def _usage_seal(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.rollup import handle_usage_seal
    return handle_usage_seal(payload)


def _usage_reconcile(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.reconcile import handle_usage_reconcile
    return handle_usage_reconcile(payload)


def _automation_execute(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.automation import handle_automation_execute
    return handle_automation_execute(payload)


def _document_verify(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.verification import handle_document_verify
    return handle_document_verify(payload)


def _billing_reconcile(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.billing import handle_billing_reconcile
    return handle_billing_reconcile(payload)


def _billing_seat_sync(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.billing import handle_billing_seat_sync
    return handle_billing_seat_sync(payload)


def _billing_seat_drift(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.billing import handle_billing_seat_drift
    return handle_billing_seat_drift(payload)


def _billing_assemble_invoice(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.billing import handle_billing_assemble_invoice
    return handle_billing_assemble_invoice(payload)


def _billing_dunning_sweep(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.billing import handle_billing_dunning_sweep
    return handle_billing_dunning_sweep(payload)


def _billing_addon_grace_sweep(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.billing import handle_billing_addon_grace_sweep
    return handle_billing_addon_grace_sweep(payload)


def _identity_recheck_domains(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.identity_jobs import handle_recheck_domains
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        return handle_recheck_domains(db, payload)


def _identity_purge_assertions(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.identity_jobs import handle_purge_assertion_payloads
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        return handle_purge_assertion_payloads(db, payload)


def _identity_sweep_replay_guard(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.identity_jobs import handle_sweep_replay_guard
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        return handle_sweep_replay_guard(db, payload)


def _identity_sweep_auth_requests(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.identity_jobs import handle_sweep_auth_requests
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        return handle_sweep_auth_requests(db, payload)


def _domain_verify_dns(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.branding import handle_domain_verify_dns
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        return handle_domain_verify_dns(db, payload)


def _tls_renew_sweep(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.branding import handle_tls_renew_sweep
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        return handle_tls_renew_sweep(db, payload)


def _redaction_detect(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.redaction import handle_redaction_detect
    return handle_redaction_detect(payload)


def _redaction_apply(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.redaction import handle_redaction_apply
    return handle_redaction_apply(payload)


def _analytics_export_sync(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.analytics import handle_export_sync
    return handle_export_sync(payload)


def _analytics_warehouse_push(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.analytics import handle_warehouse_push
    return handle_warehouse_push(payload)


def _procurement_score(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.procurement import handle_procurement_score
    return handle_procurement_score(payload)


def _partner_rev_share_compute(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.partner import handle_rev_share_compute
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        return handle_rev_share_compute(db, payload)


def _partner_rev_share_seal(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.partner import handle_rev_share_seal
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        return handle_rev_share_seal(db, payload)


def _anomaly_scan_document(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.radar import handle_anomaly_scan_document
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        result = handle_anomaly_scan_document(db, payload)
        db.commit()
        return result


def _anomaly_nightly(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.radar import handle_anomaly_nightly
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        # The nightly handler commits per workspace itself, so one tenant's
        # malformed extraction cannot roll back another tenant's findings.
        return handle_anomaly_nightly(db, payload)


_HANDLERS = {
    "document.extract": _document_extract,
    "document.enrich": _document_enrich,
    "storage.sample": _storage_sample,
    "knowledge.reindex": _knowledge_reindex,
    "notification.deliver": _notification_deliver,
    "usage.rollup": _usage_rollup,
    "usage.seal": _usage_seal,
    "usage.reconcile": _usage_reconcile,
    "automation.execute": _automation_execute,
    "document.verify": _document_verify,
    "billing.reconcile": _billing_reconcile,
    "billing.seat_sync": _billing_seat_sync,
    "billing.seat_drift": _billing_seat_drift,
    "billing.assemble_invoice": _billing_assemble_invoice,
    "billing.dunning_sweep": _billing_dunning_sweep,
    "billing.addon_grace_sweep": _billing_addon_grace_sweep,
    "identity.recheck_domains": _identity_recheck_domains,
    "identity.purge_assertion_payloads": _identity_purge_assertions,
    "identity.sweep_replay_guard": _identity_sweep_replay_guard,
    "identity.sweep_auth_requests": _identity_sweep_auth_requests,
    # ARCH-25. Both are also listed on the LIGHT profile in
    # app/workers/profiles.py; a handler here with no profile there is a job
    # that enqueues cleanly and never runs.
    "domain.verify_dns": _domain_verify_dns,
    "tls.renew_sweep": _tls_renew_sweep,
    # ARCH-26. Both are also listed on the LIGHT profile in
    # app/workers/profiles.py; a handler here with no profile there is a job
    # that enqueues cleanly and never runs.
    "analytics.export_sync": _analytics_export_sync,
    "analytics.warehouse_push": _analytics_warehouse_push,
    # ARCH-27. Both are also listed on the LIGHT profile in
    # app/workers/profiles.py; a handler here with no profile there is a job
    # that enqueues cleanly and never runs.
    "partner.rev_share_compute": _partner_rev_share_compute,
    "partner.rev_share_seal": _partner_rev_share_seal,
    # ARCH-31. Also listed on the LIGHT profile in
    # app/workers/profiles.py and in DEFAULT_SCHEDULE in
    # app/workers/scheduler.py. A handler here with no profile there
    # is a job that enqueues cleanly and never runs — and
    # assert_imports_match_profile() raises ProfileError at every
    # worker's startup on a handler no profile claims, so registering
    # this without the profile entry stops the entire fleet booting.
    "procurement.score": _procurement_score,
    # ARCH-32. Also claimed by the OCR profile in
    # app/workers/profiles.py. NOT in DEFAULT_SCHEDULE: neither job is
    # recurring, and re-running an apply on a schedule would re-render
    # and re-upload a document somebody may have cancelled. A handler
    # here with no profile there is a job that enqueues cleanly and
    # never runs -- and assert_imports_match_profile() raises
    # ProfileError at every worker's startup on a handler no profile
    # claims, so registering these without the profile entry stops the
    # entire fleet booting.
    "redaction.detect": _redaction_detect,
    "redaction.apply": _redaction_apply,
    # ARCH34-S2:radar-handlers. Both are also listed on the LIGHT profile
    # in app/workers/profiles.py, and `anomaly.nightly` is in
    # DEFAULT_SCHEDULE in app/workers/scheduler.py. A handler here with no
    # profile there is a job that enqueues cleanly and never runs — and
    # assert_imports_match_profile() raises ProfileError at every worker's
    # startup on a handler no profile claims, so registering these without
    # the profile entry stops the entire fleet booting.
    "anomaly.scan_document": _anomaly_scan_document,
    "anomaly.nightly": _anomaly_nightly,
}


def _assert_vocabulary_matches_registry() -> None:
    """ARCH-27 CF2. Give the per-phase constants a consumer.

    A frozenset nothing reads is a comment with a type annotation. Comparing
    the union against the registry means a job type added to one and not the
    other raises here, at import, naming both sides — rather than surfacing
    weeks later as a queue that never drains.
    """
    registered = frozenset(_HANDLERS)
    undeclared = registered - ALL_PHASE_JOB_TYPES
    unregistered = ALL_PHASE_JOB_TYPES - registered
    if undeclared or unregistered:
        raise RuntimeError(
            "job type vocabulary and handler registry disagree. "
            f"registered but undeclared: {sorted(undeclared)}; "
            f"declared but unregistered: {sorted(unregistered)}. "
            "Update the ARCHnn_JOB_TYPES constant for the owning phase."
        )


_assert_vocabulary_matches_registry()


def register_all(*, replace: bool = False) -> list[str]:
    registered: list[str] = []
    for job_type, handler in _HANDLERS.items():
        existing = job_service.JOB_HANDLERS.get(job_type)
        if existing is handler:
            continue
        if existing is not None:
            if not replace:
                raise job_service.JobServiceError(
                    f"job_type {job_type!r} is already registered to {existing!r}."
                )
            job_service.JOB_HANDLERS[job_type] = handler
        else:
            job_service.register_handler(job_type, handler)
        registered.append(job_type)

    if registered:
        logger.info("jobs.handlers_registered", extra={"job_types": registered})
    return registered


__all__ = [
    "ARCH10_JOB_TYPES",
    "ARCH11_JOB_TYPES",
    "ARCH12_JOB_TYPES",
    "ARCH13_JOB_TYPES",
    "ARCH14_JOB_TYPES",
    "ARCH15_JOB_TYPES",
    "ARCH16_JOB_TYPES",
    "ARCH25_JOB_TYPES",
    "ARCH26_JOB_TYPES",
    "ARCH27_JOB_TYPES",
    "ARCH31_JOB_TYPES",
    "ARCH32_JOB_TYPES",
    "ARCH34_JOB_TYPES",
    "ALL_PHASE_JOB_TYPES",
    "register_all",
]
