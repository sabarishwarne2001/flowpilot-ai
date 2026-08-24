"""ARCH-10 Step 6/7, ARCH-11 Step 4, ARCH-12 Step 7, ARCH-13 Step 13.5/13.7, ARCH-14 Step 2 & 5, ARCH-15 Step 15.2/15.4 — job handler registration."""

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
    {"billing.reconcile", "billing.seat_sync", "billing.seat_drift"}
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
}


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
    "register_all",
]