"""Helm's refusals, in the platform's error envelope ({code, message, details}).

The exception handler reads `status_code` and `code` from the class, so there is one class per
status; the specific reason travels in `details["reason"]`.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from app.core.exceptions import FlowPilotError


class HelmError(FlowPilotError):
    status_code = 409
    code = "HELM_CONFLICT"

    def __init__(self, message: str, *, reason: Optional[str] = None, details: Optional[Mapping[str, Any]] = None):
        super().__init__(message)
        self.details: dict[str, Any] = dict(details or {})
        if reason:
            self.details["reason"] = reason


class HelmForbidden(HelmError):
    status_code = 403
    code = "HELM_FORBIDDEN"


class HelmNotFound(HelmError):
    status_code = 404
    code = "NOT_FOUND"


class HelmInvalid(HelmError):
    status_code = 422
    code = "HELM_INVALID"


class HelmQuota(HelmError):
    """The organization's AI usage limit refuses a model call (drafting a mandate from a sentence)."""

    status_code = 402
    code = "AI_LIMIT_REACHED"


__all__ = ["HelmError", "HelmForbidden", "HelmInvalid", "HelmNotFound", "HelmQuota"]
