"""ARCH-10 Step 6/7, ARCH-11 Step 4, ARCH-12 Step 7 & ARCH-14 Step 2 — job handler registration."""

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
ARCH14_JOB_TYPES: frozenset[str] = frozenset({"usage.rollup", "usage.seal"})


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


_HANDLERS = {
    "document.extract": _document_extract,
    "document.enrich": _document_enrich,
    "storage.sample": _storage_sample,
    "knowledge.reindex": _knowledge_reindex,
    "notification.deliver": _notification_deliver,
    "usage.rollup": _usage_rollup,
    "usage.seal": _usage_seal,
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
                    f"job_type {job_type!r} is already registered to "
                    f"{existing!r}; refusing to shadow it."
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
    "ARCH14_JOB_TYPES",
    "register_all",
]